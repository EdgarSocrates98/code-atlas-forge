"""lf-0133 Phase 7 — ``domain_fts`` dedicado para entidades de domínio.

FTS5 standalone (rowid = ``domain_entity_occurrences.id``), populado em
``persist_domain_result`` — só snapshots novos; pré-v12 ficam no LIKE
bounded com fallback **declarado** (``domain-fts-fallback``).
``Store.rebuild_domain_fts`` é o backfill por snapshot; o GC apaga as
rows junto com o snapshot (FTS não é FK). Semânticas honestamente
diferentes: LIKE é substring ASCII; FTS5 ``unicode61`` casa por token
com case-fold e remoção de diacríticos, e ``terms`` cobre fragmentos
de identificador (``mart.orders`` casa ``mart.v_orders`` por token).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from code_atlas.domains.registry import DomainRegistry
from code_atlas.domains.sql import SqlDomainAdapter
from code_atlas.indexing import IndexCoordinator
from code_atlas.search import SearchEngine, SearchRequest
from code_atlas.search.domain import DomainEntitySearch
from code_atlas.storage import connect, migrate
from code_atlas.storage.db import fts5_available
from code_atlas.storage.store import Store

pytestmark = pytest.mark.skipif(
    not fts5_available(sqlite3.connect(":memory:")),
    reason="build sem FTS5 — domain_fts é FTS5-gated (v12)",
)

_REPO = {
    "q.sql": (
        "CREATE VIEW mart.v_orders AS SELECT * FROM mart.orders;\n"
        "CREATE VIEW mart.v_sao_paulo AS SELECT * FROM mart.fact_sp;\n"
    ),
}


def _registry(repo: Path) -> DomainRegistry:
    reg = DomainRegistry()
    reg.register(SqlDomainAdapter(base_path=repo))
    return reg


@pytest.fixture()
def env(tmp_path: Path):
    repo = tmp_path / "repo"
    for rel, content in _REPO.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    out = IndexCoordinator(
        conn, domain_registry=_registry(repo)).index(repo)
    assert out.status == "success" and out.snapshot_id is not None
    yield conn, str(repo), out.snapshot_id
    conn.close()


def _domain_facet(conn, root, snap, q: str, **kw):
    """Chama a faceta diretamente — escopo explícito de snapshot."""
    req = SearchRequest(query=q, repository_root=root, **kw)
    repo_id = conn.execute("SELECT MIN(id) FROM repositories").fetchone()[0]
    return DomainEntitySearch(conn).search(req, repo_id, snap)


def test_persist_populates_domain_fts(env) -> None:
    conn, _root, snap = env
    occ = conn.execute(
        "SELECT COUNT(*) FROM domain_entity_occurrences"
        " WHERE snapshot_id = ?", (snap,)).fetchone()[0]
    fts = conn.execute(
        "SELECT COUNT(*) FROM domain_fts WHERE snapshot_id = ?",
        (snap,)).fetchone()[0]
    assert occ > 0 and fts == occ
    # rowid = id da ocorrência — mesmo desenho de fts_occurrences
    orphan = conn.execute(
        "SELECT COUNT(*) FROM domain_fts f"
        " WHERE NOT EXISTS (SELECT 1 FROM domain_entity_occurrences d"
        "                  WHERE d.id = f.rowid)").fetchone()[0]
    assert orphan == 0


def test_fts_hits_and_no_fallback_diag_on_covered_snapshot(env) -> None:
    conn, root, snap = env
    items, _total, diags = _domain_facet(conn, root, snap, "orders")
    assert items
    names = {h.qualified_name for h in items}
    assert "orders" in names and "v_orders" in names
    assert not any(d.code.startswith("domain-fts") for d in diags)
    # contrato preservado: exato pontua 2.0 e ordena primeiro
    assert items[0].qualified_name == "orders" and items[0].score == 2.0


def test_fts_token_fragment_marks_terms_honestly(env) -> None:
    conn, root, snap = env
    # "mart.orders" não é substring de "mart.v_orders"/"orders" — a
    # casa é por token; matched_fields declara ``terms``.
    items, _t, _d = _domain_facet(conn, root, snap, "mart.orders")
    assert items
    assert any("terms" in h.matched_fields for h in items)


def test_fts_prefix_and_terms_columns(env) -> None:
    conn, root, snap = env
    items, _t, _d = _domain_facet(conn, root, snap, "order*")
    assert {h.qualified_name for h in items} >= {"orders", "v_orders"}


def test_uncovered_snapshot_falls_back_to_like_declared(env) -> None:
    """Snapshot sem rows FTS → LIKE bounded + ``domain-fts-fallback``."""
    conn, root, snap = env
    conn.execute("DELETE FROM domain_fts WHERE snapshot_id = ?", (snap,))
    items, _t, diags = _domain_facet(conn, root, snap, "orders")
    assert any(d.code == "domain-fts-fallback" for d in diags)
    # o LIKE continua achando o que sempre achou
    assert "orders" in {h.qualified_name for h in items}


def test_missing_fts_table_is_silent_like_old_schema(env) -> None:
    """Banco pré-v12 (tabela ausente): LIKE sem diagnóstico — nada mudou."""
    conn, root, snap = env
    conn.execute("DROP TABLE domain_fts")
    items, _t, diags = _domain_facet(conn, root, snap, "orders")
    assert "orders" in {h.qualified_name for h in items}
    assert not any(d.code.startswith("domain-fts") for d in diags)


def test_rebuild_domain_fts_backfills_snapshot(env) -> None:
    conn, root, snap = env
    store = Store(conn)
    conn.execute("DELETE FROM domain_fts WHERE snapshot_id = ?", (snap,))
    assert not DomainEntitySearch(conn)._fts_usable_for(snap)
    n = store.rebuild_domain_fts(snap)
    occ = conn.execute(
        "SELECT COUNT(*) FROM domain_entity_occurrences"
        " WHERE snapshot_id = ?", (snap,)).fetchone()[0]
    assert n == occ
    items, _t, diags = _domain_facet(conn, root, snap, "orders")
    assert items and not any(
        d.code == "domain-fts-fallback" for d in diags)


def test_fts_scope_is_snapshot_and_repository(env, tmp_path: Path) -> None:
    """Rows de outro snapshot/repo nunca vazam para a faceta."""
    conn, root, snap1 = env
    # snapshot 2: repo editado → novo READY; snap1 vira STALE com rows.
    repo_p = Path(root)
    (repo_p / "q2.sql").write_text(
        "CREATE VIEW mart.v_extra AS SELECT * FROM mart.orders;\n")
    out = IndexCoordinator(
        conn, domain_registry=_registry(repo_p)).index(
            repo_p, force=True)
    assert out.status == "success" and out.snapshot_id != snap1
    snap2 = out.snapshot_id
    # snap1 (STALE) ainda tem suas rows e responde pelo próprio escopo
    items1, _t1, _d1 = _domain_facet(conn, root, snap1, "orders")
    items2, _t2, _d2 = _domain_facet(conn, root, snap2, "orders")
    assert all(h.snapshot_id == snap1 for h in items1)
    assert all(h.snapshot_id == snap2 for h in items2)
    # a entidade nova só existe no snap2 — nunca vaza para trás
    assert "v_extra" in {h.qualified_name for h in items2}
    assert "v_extra" not in {h.qualified_name for h in items1}


def test_fts_accent_behavior_honest_difference(env) -> None:
    """unicode61 remove diacríticos: ``sao`` casa ``São`` só no FTS.

    LIKE é substring ASCII case-insensitive — ``sao`` não casa ``São``.
    A diferença é testada nos dois caminhos para nunca ser assumida.
    """
    conn, root, snap = env
    conn.execute(
        "UPDATE domain_entities SET name = 'São Orders'"
        " WHERE qualified_name = 'orders'")
    Store(conn).rebuild_domain_fts(snap)
    items, _t, _d = _domain_facet(conn, root, snap, "sao")
    assert any(h.name == "São Orders" for h in items)
    # mesma query no LIKE: ``sao`` não é substring de ``São`` — zero.
    conn.execute("DELETE FROM domain_fts WHERE snapshot_id = ?", (snap,))
    items_like, _tl, _dl = _domain_facet(conn, root, snap, "sao")
    assert not any(h.name == "São Orders" for h in items_like)
    # e o LIKE ainda casa o caminho ASCII normal
    items_ok, _to, _do = _domain_facet(conn, root, snap, "orders")
    assert items_ok


def test_gc_stale_snapshots_removes_domain_fts_rows(env) -> None:
    conn, root, _snap1 = env
    repo_p = Path(root)
    (repo_p / "q2.sql").write_text(
        "CREATE VIEW mart.v_extra AS SELECT * FROM mart.orders;\n")
    out = IndexCoordinator(
        conn, domain_registry=_registry(repo_p)).index(
            repo_p, force=True)
    assert out.status == "success"
    store = Store(conn)
    doomed = conn.execute(
        "SELECT id FROM snapshots WHERE status='STALE'").fetchall()
    assert doomed
    removed = store.gc_stale_snapshots(
        repository_id=conn.execute(
            "SELECT MIN(id) FROM repositories").fetchone()[0],
        keep=0,
    )
    assert removed == len(doomed)
    left = conn.execute(
        "SELECT COUNT(*) FROM domain_fts f"
        " WHERE f.snapshot_id NOT IN (SELECT id FROM snapshots)"
    ).fetchone()[0]
    assert left == 0
    # snapshot vivo mantém cobertura
    ready = conn.execute(
        "SELECT id FROM snapshots WHERE status='READY'").fetchone()[0]
    live = conn.execute(
        "SELECT COUNT(*) FROM domain_fts WHERE snapshot_id = ?",
        (ready,)).fetchone()[0]
    assert live > 0


def test_fts_ordering_deterministic_across_calls(env) -> None:
    conn, root, snap = env
    a, _ta, _da = _domain_facet(conn, root, snap, "orders")
    b, _tb, _db = _domain_facet(conn, root, snap, "orders")
    assert [(h.entity_id, h.qualified_name) for h in a] == [
        (h.entity_id, h.qualified_name) for h in b]


def test_via_relation_union_preserves_contract(env) -> None:
    """Expansão entity↔entity segue citando aresta real em evidence."""
    conn, root, _snap = env
    res = SearchEngine(conn).search(
        SearchRequest(query="orders", repository_root=root))
    related = [h for h in res.domain_items
               if h.matched_fields == ("via_relation",)]
    assert related, "v_orders lê mart.orders — aresta real esperada"
    for h in related:
        assert any(e.startswith("via=") and ":rel:" in e
                   for e in h.evidence)
        assert h.score < 1.0
