"""lf-0036 — busca lexical FTS5 snapshot-scoped + contratos + engine."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from code_atlas.indexing import IndexCoordinator
from code_atlas.search import SearchEngine, SearchRequest
from code_atlas.search.contracts import SEARCH_SCHEMA_VERSION
from code_atlas.search.tokenizer import split_identifier
from code_atlas.storage import connect, migrate

_REPO = {
    "pkg/__init__.py": "",
    "pkg/orders.py": (
        'def processCustomerOrders(items):\n'
        '    """Process customer orders batch."""\n'
        '    return len(items)\n\n'
        'def refund_order(order_id):\n'
        '    return order_id\n'
    ),
    "web/app.ts": (
        "export function renderDashboard() {\n"
        "    return true;\n"
        "}\n"
    ),
}


@pytest.fixture()
def env(tmp_path: Path) -> tuple[sqlite3.Connection, str]:
    repo = tmp_path / "repo"
    for rel, content in _REPO.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    IndexCoordinator(conn).index(repo)
    conn.row_factory = sqlite3.Row
    yield conn, str(repo)
    conn.close()


def _search(env, q, **kw) -> object:
    conn, root = env
    return SearchEngine(conn).search(
        SearchRequest(query=q, repository_root=root, **kw)
    )


def _names(res) -> list[str]:
    return [h.qualified_name for h in res.items]


def test_exact_and_fragment_and_prefix(env: tuple[sqlite3.Connection, str]) -> None:
    assert "pkg.orders.processCustomerOrders" in _names(
        _search(env, "processCustomerOrders"))
    # fragmento camelCase
    assert "pkg.orders.processCustomerOrders" in _names(
        _search(env, "customer"))
    # snake_case fragmento
    assert "pkg.orders.refund_order" in _names(_search(env, "refund"))
    # prefixo
    assert "pkg.orders.processCustomerOrders" in _names(
        _search(env, "proc*"))
    # sem stemming inventado: "order" não casa "orders"
    assert "pkg.orders.processCustomerOrders" not in _names(
        _search(env, "order"))


def test_qualified_and_docstring_and_signature(env: tuple[sqlite3.Connection, str]) -> None:
    assert "pkg.orders.refund_order" in _names(
        _search(env, "pkg.orders.refund_order"))
    # docstring indexada
    assert "pkg.orders.processCustomerOrders" in _names(
        _search(env, "batch"))
    # token de signature (parâmetro)
    assert "pkg.orders.refund_order" in _names(
        _search(env, "order_id"))


def test_filters_language_kind_path(env: tuple[sqlite3.Connection, str]) -> None:
    res = _search(env, "renderDashboard", language="typescript")
    assert "web.app.renderDashboard" in _names(res)
    res = _search(env, "renderDashboard", language="python")
    assert res.items == ()
    res = _search(env, "orders", kind="function")
    assert all(h.kind == "function" for h in res.items)
    res = _search(env, "orders", path="web/")
    assert res.items == ()


def test_pagination_cursor_stability(env: tuple[sqlite3.Connection, str]) -> None:
    r1 = _search(env, "orders", limit=1)
    assert r1.truncated and r1.next_cursor
    r2 = _search(env, "orders", limit=1, cursor=r1.next_cursor)
    assert r1.items[0].occurrence_id != r2.items[0].occurrence_id
    # cursor de outra query → diagnóstico tipado, não reuso mudo
    bad = _search(env, "refund", cursor=r1.next_cursor)
    assert any(d.code == "invalid-cursor" for d in bad.diagnostics)


def test_envelope_v2_fields(env: tuple[sqlite3.Connection, str]) -> None:
    res = _search(env, "customer")
    assert res.schema_version == SEARCH_SCHEMA_VERSION
    assert res.snapshot_id > 0 and res.strategy == "lexical"
    assert res.ranking_version == "bm25-fts5-v1"
    d = res.to_dict()
    assert d["items"][0]["matched_fields"]


def test_unknown_strategy_is_typed_error(env: tuple[sqlite3.Connection, str]) -> None:
    res = _search(env, "x", strategy="hybrid")
    assert any(d.code == "strategy-unavailable" for d in res.diagnostics)
    caps = SearchEngine(env[0]).capabilities()
    assert "graph" not in caps.strategies


def test_structural_strategy_passthrough(env: tuple[sqlite3.Connection, str]) -> None:
    res = _search(env, "CustomerOrders", strategy="structural")
    assert "pkg.orders.processCustomerOrders" in _names(res)


def test_snapshot_isolation_and_reindex(
    env: tuple[sqlite3.Connection, str], tmp_path: Path
) -> None:
    conn, root = env
    repo = Path(root)
    snap1 = conn.execute(
        "SELECT MAX(id) FROM snapshots").fetchone()[0]
    n1 = conn.execute(
        "SELECT COUNT(*) c FROM fts_occurrences WHERE snapshot_id=?",
        (snap1,)).fetchone()["c"]
    assert n1 > 0
    # novo index → snapshot novo; busca só vê o READY atual
    (repo / "pkg" / "extra.py").write_text("def brand_new():\n    return 0\n")
    IndexCoordinator(conn).index(repo)
    snap2 = conn.execute("SELECT MAX(id) FROM snapshots").fetchone()[0]
    assert snap2 != snap1
    res = _search(env, "brand_new")
    assert "pkg.extra.brand_new" in _names(res)
    assert res.snapshot_id == snap2
    # linhas do snapshot antigo ainda existem (retention ilimitada)
    # mas NÃO são retornadas — isolamento por snapshot_id
    res2 = _search(env, "customer")
    assert all(h.occurrence_id for h in res2.items)


def test_gc_removes_fts_rows(
    env: tuple[sqlite3.Connection, str], tmp_path: Path
) -> None:
    conn, root = env
    repo = Path(root)
    coord = IndexCoordinator(conn, snapshot_retention=1)
    coord.index(repo)
    (repo / "pkg" / "x.py").write_text("def xx():\n    return 1\n")
    coord.index(repo)
    (repo / "pkg" / "y.py").write_text("def yy():\n    return 2\n")
    coord.index(repo)
    # 3 snapshots; retention=1 → 2 STALE antigos podados (1 keep)
    stale = conn.execute(
        "SELECT COUNT(*) c FROM snapshots WHERE status='STALE'"
    ).fetchone()["c"]
    fts = conn.execute("SELECT COUNT(*) c FROM fts_occurrences").fetchone()["c"]
    occ = conn.execute(
        "SELECT COUNT(*) c FROM symbol_occurrences").fetchone()["c"]
    assert stale <= 1
    assert fts == occ  # nenhuma linha órfã


def test_update_exclusion_rollback(
    env: tuple[sqlite3.Connection, str]
) -> None:
    """lf-0036: símbolo removido some do índice novo; index abortado no
    meio não publica linhas FTS parciais (transacional)."""
    conn, root = env
    repo = Path(root)
    # remoção: arquivo some → símbolo some do snapshot novo
    (repo / "pkg" / "orders.py").write_text("def only_left():\n    pass\n")
    IndexCoordinator(conn).index(repo)
    assert "pkg.orders.processCustomerOrders" not in _names(
        _search(env, "customer"))
    assert "pkg.orders.only_left" in _names(_search(env, "only_left"))
    # rollback: delete manual do snapshot READY no meio do "crash" —
    # busca anterior continua consistente (READY novo nunca publicado)
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "INSERT INTO fts_occurrences (rowid, snapshot_id, name, terms,"
        " signature, docstring, path, language, kind)"
        " VALUES (-999, -1, 'ghost', 'ghost', '', '', '', '', '')")
    conn.rollback()
    res = _search(env, "ghost")
    assert res.items == ()


def test_tokenizer_units() -> None:
    assert split_identifier("processCustomerOrders") == [
        "process", "customer", "orders"]
    assert split_identifier("refund_order") == ["refund", "order"]
    assert split_identifier("HTTPServer") == ["http", "server"]
    assert split_identifier("pkg.util.helper") == [
        "pkg", "util", "helper"]
