"""lf-0119 Phase 11 — busca federada multi-repo (engine).

A busca roda no escopo ``(repo, snapshot)`` de cada membro — resolvido
pela view (pin → override → latest-READY) — e o merge é posicional por
rank de membro. Invariâncias centrais testadas aqui: homônimos
cross-repo NUNCA colapsam, todo item sai tagueado
``repository_id``+``snapshot_id``, pinned-vs-latest é observável e a
ordenação é determinística.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from code_atlas.domains.api import ApiContractAdapter
from code_atlas.domains.registry import DomainRegistry
from code_atlas.domains.sql import SqlDomainAdapter
from code_atlas.federation import Federation
from code_atlas.indexing import IndexCoordinator
from code_atlas.search import SearchRequest
from code_atlas.search.federated import FederatedSearchEngine
from code_atlas.storage import connect, migrate
from code_atlas.storage.store import Store

_OPENAPI = """\
openapi: 3.0.0
info:
  title: Orders API
  version: 1.0.0
servers:
  - url: https://api.orders.example/v1
paths:
  /orders:
    get:
      operationId: listOrders
      responses:
        '200':
          description: all orders
"""

# Dois repos com o MESMO contrato e a MESMA tabela mart.orders (mesma
# shared_key) + função homônima ``process_orders`` nos dois — o caso
# que nunca pode colapsar.
_REPO_A = {
    "spec/openapi.yaml": _OPENAPI,
    "q.sql": "CREATE VIEW mart.v_orders AS SELECT * FROM mart.orders;\n",
    "app/orders.py": (
        "def process_orders(items):\n    return [i for i in items]\n"
    ),
}
_REPO_B = {
    "spec/openapi.yaml": _OPENAPI,
    "q.sql": "SELECT * FROM mart.orders;\nSELECT * FROM other.orders;\n",
    "app/orders.py": (
        "def process_orders(rows):\n    return sorted(rows)\n"
    ),
}


def _index(conn: sqlite3.Connection, root: Path) -> int:
    reg = DomainRegistry()
    reg.register(SqlDomainAdapter(base_path=root))
    reg.register(ApiContractAdapter(workspace_root=root))
    out = IndexCoordinator(conn, domain_registry=reg).index(root)
    assert out.status == "success" and out.snapshot_id is not None
    return out.snapshot_id


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


@pytest.fixture()
def fed_env(tmp_path: Path):
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    root_a = _write(tmp_path / "repo_a", dict(_REPO_A))
    root_b = _write(tmp_path / "repo_b", dict(_REPO_B))
    snap_a = _index(conn, root_a)
    snap_b = _index(conn, root_b)
    store = Store(conn)
    sa = store.snapshot_by_id(snap_a)
    sb = store.snapshot_by_id(snap_b)
    assert sa is not None and sb is not None
    fed = Federation(conn)
    ws = fed.create_workspace("orders-fed")
    fed.add_member(ws, sa.repository_id)
    fed.add_member(ws, sb.repository_id)
    ids = {
        "repo_a": sa.repository_id, "repo_b": sb.repository_id,
        "snap_a": snap_a, "snap_b": snap_b,
        "root_a": root_a, "root_b": root_b, "ws": ws.id,
    }
    yield conn, ids
    conn.close()


def _search(conn: sqlite3.Connection, ids: dict[str, object],
            q: str, **kw):
    return FederatedSearchEngine(conn).search(
        SearchRequest(query=q, **kw), ids["ws"])  # type: ignore[arg-type]


def test_contract_found_in_both_repos_tagged_distinctly(fed_env) -> None:
    conn, ids = fed_env
    res = _search(conn, ids, "orders")
    assert res.view.view_id.startswith("fedview:")
    doms = [
        h for h in res.domain_items
        if h.qualified_name == "orders" and h.namespace == "mart"]
    assert len(doms) == 2
    assert {h.repository_id for h in doms} == {
        ids["repo_a"], ids["repo_b"]}
    # mesma entidade, snapshots diferentes por membro — nunca fundida
    assert {h.snapshot_id for h in doms} == {
        ids["snap_a"], ids["snap_b"]}


def test_same_name_symbols_never_collapse(fed_env) -> None:
    conn, ids = fed_env
    res = _search(conn, ids, "process_orders", strategy="hybrid")
    hits = [h for h in res.items
            if h.qualified_name.endswith("process_orders")]
    assert {h.repository_id for h in hits} == {
        ids["repo_a"], ids["repo_b"]}
    assert all(h.snapshot_id is not None for h in hits)
    # um item por repo — nenhum colapso por qname idêntico
    assert len({(h.repository_id, h.occurrence_id) for h in hits}) == \
        len(hits)


def test_repository_filter_narrows_honestly(fed_env) -> None:
    conn, ids = fed_env
    res = FederatedSearchEngine(conn).search(
        SearchRequest(query="orders"), ids["ws"],  # type: ignore[arg-type]
        repository_id=int(ids["repo_b"]))  # type: ignore[arg-type]
    assert res.members_searched == 1
    assert {h.repository_id for h in res.domain_items} == {ids["repo_b"]}
    assert {h.repository_id for h in res.items} <= {ids["repo_b"]}
    assert res.repository_filter == ids["repo_b"]
    # repo fora do workspace → narrowing declara vazio
    res2 = FederatedSearchEngine(conn).search(
        SearchRequest(query="orders"), ids["ws"],  # type: ignore[arg-type]
        repository_id=99999)
    assert res2.members_searched == 0
    assert any(d.code == "member-filter-empty" for d in res2.diagnostics)


def test_filters_narrow_domain_facet(fed_env) -> None:
    conn, ids = fed_env
    res = _search(conn, ids, "orders", domain="sql")
    assert res.domain_items
    assert all(h.domain == "sql" for h in res.domain_items)
    res = _search(conn, ids, "orders", kind="data_table")
    assert {h.qualified_name for h in res.domain_items} == {"orders"}


def test_pinned_vs_latest_snapshot_observable(fed_env) -> None:
    conn, ids = fed_env
    fse = FederatedSearchEngine(conn)
    root_a = Path(str(ids["root_a"]))
    (root_a / "q.sql").write_text(
        "SELECT * FROM mart.orders;\nSELECT * FROM mart.extra;\n")
    snap_a2 = _index(conn, root_a)
    # latest-READY: mart.extra existe; pinado no snap antigo: invisível
    latest = fse.search(SearchRequest(query="extra"), ids["ws"])  # type: ignore[arg-type]
    assert any(h.qualified_name == "extra"
               and h.repository_id == ids["repo_a"]
               and h.snapshot_id == snap_a2
               for h in latest.domain_items)
    pinned = fse.search(
        SearchRequest(query="extra"), ids["ws"],  # type: ignore[arg-type]
        pins={int(ids["repo_a"]): int(ids["snap_a"])})  # type: ignore[arg-type]
    assert not any(h.qualified_name == "extra"
                   for h in pinned.domain_items)
    assert pinned.view.view_id != latest.view.view_id
    # snapshot_id como açúcar de pin → mesma view do pin explícito
    sugared = fse.search(
        SearchRequest(query="extra", snapshot_id=int(ids["snap_a"])),  # type: ignore[arg-type]
        ids["ws"])  # type: ignore[arg-type]
    assert sugared.view.view_id == pinned.view.view_id


def test_view_snapshot_map_matches_member_tags(fed_env) -> None:
    conn, ids = fed_env
    res = _search(conn, ids, "orders")
    snap_map = {m.repository_id: m.snapshot_id
                for m in res.view.members}
    for h in res.domain_items:
        assert h.snapshot_id == snap_map[h.repository_id]
    for h in res.items:
        assert h.snapshot_id == snap_map[h.repository_id]


def _hit_key(h) -> tuple:
    return (h.repository_id, h.snapshot_id, h.occurrence_id,
            h.qualified_name)


def _dom_key(h) -> tuple:
    return (h.repository_id, h.entity_id)


def test_determinism_same_query_twice(fed_env) -> None:
    conn, ids = fed_env
    a = _search(conn, ids, "orders")
    b = _search(conn, ids, "orders")
    assert a.view.view_id == b.view.view_id
    assert [_hit_key(h) for h in a.items] == \
           [_hit_key(h) for h in b.items]
    assert [_dom_key(h) for h in a.domain_items] == \
           [_dom_key(h) for h in b.domain_items]


def test_bounded_limit_and_cursor(fed_env) -> None:
    conn, ids = fed_env
    res = _search(conn, ids, "orders", limit=2)
    assert len(res.domain_items) <= 2
    res_page = _search(conn, ids, "orders", limit=1)
    assert len(res_page.items) <= 1
    if res_page.next_cursor:
        page2 = _search(conn, ids, "orders", limit=1,
                        cursor=res_page.next_cursor)
        assert not any(d.code == "invalid-cursor"
                       for d in page2.diagnostics)
        first = {(h.repository_id, h.occurrence_id)
                 for h in res_page.items}
        assert first.isdisjoint(
            {(h.repository_id, h.occurrence_id) for h in page2.items})
    # cursor de outro fingerprint (outra query/view) → rejeitado
    from code_atlas.search.contracts import encode_cursor
    bad = _search(conn, ids, "orders", limit=1,
                  cursor=encode_cursor(0, "deadbeefcafe"))
    assert any(d.code == "invalid-cursor" for d in bad.diagnostics)


def test_workspace_and_view_identity_in_result(fed_env) -> None:
    conn, ids = fed_env
    res = _search(conn, ids, "orders")
    payload = res.to_dict()
    assert payload["workspace_id"] == ids["ws"]
    assert payload["view_id"] == res.view.view_id
    assert {m["repository_id"] for m in payload["members"]} == {
        ids["repo_a"], ids["repo_b"]}
    assert payload["members_searched"] == 2
