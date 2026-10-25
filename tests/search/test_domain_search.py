"""lf-0119 Phase 11 — faceta de busca sobre entidades de domínio.

A faceta ``domain_items`` é separada de ``items`` por desenho: LIKE
bounded snapshot-scoped sobre ``domain_entity_occurrences`` +
``domain_entities``, tipada (kind/domain/repository/snapshot_id/
evidence count), nunca fundida com hits de símbolo. ``via_relation``
cobre o relationship-search mínimo: endpoints entity↔entity
resolvidos adjacentes a um match direto.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from code_atlas.domains.api import ApiContractAdapter
from code_atlas.domains.registry import DomainRegistry
from code_atlas.domains.sql import SqlDomainAdapter
from code_atlas.indexing import IndexCoordinator
from code_atlas.search import SearchEngine, SearchRequest
from code_atlas.storage import connect, migrate

_REPO = {
    "spec/openapi.yaml": """\
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
""",
    "q.sql": "CREATE VIEW mart.v_orders AS SELECT * FROM mart.orders;\n",
    "app/orders.py": (
        "def process_orders(items):\n    return [i for i in items]\n"
    ),
}


@pytest.fixture()
def env(tmp_path: Path):
    repo = tmp_path / "repo"
    for rel, content in _REPO.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    reg = DomainRegistry()
    reg.register(SqlDomainAdapter(base_path=repo))
    reg.register(ApiContractAdapter(workspace_root=repo))
    out = IndexCoordinator(conn, domain_registry=reg).index(repo)
    assert out.status == "success" and out.snapshot_id is not None
    yield conn, str(repo), out.snapshot_id
    conn.close()


def _search(env, q: str, **kw):
    conn, root, _snap = env
    return SearchEngine(conn).search(
        SearchRequest(query=q, repository_root=root, **kw))


def _domains(res):
    return res.domain_items


def test_domain_facet_separate_from_symbol_hits(env) -> None:
    res = _search(env, "orders", strategy="hybrid")
    # símbolo python casa em items; entidades de domínio na faceta —
    # NUNCA misturadas
    assert any(h.qualified_name.endswith("process_orders")
               for h in res.items)
    assert all(h.item_type == "symbol" for h in res.items)
    doms = _domains(res)
    assert doms
    assert all(h.item_type == "domain_entity" for h in doms)
    assert not any(
        h.qualified_name.endswith("process_orders") for h in doms)


def test_domain_hit_typed_fields(env) -> None:
    res = _search(env, "orders")
    orders = next(
        h for h in res.domain_items
        if h.kind == "data_table" and h.qualified_name == "orders")
    assert orders.domain == "sql"
    assert orders.namespace == "mart"
    assert orders.occurrences >= 1  # evidence count
    assert orders.repository_id is not None
    assert orders.snapshot_id == res.snapshot_id
    assert "name" in orders.matched_fields
    # match exato ordena primeiro e pontua acima de substring
    assert orders.score == 2.0
    assert res.domain_items[0].qualified_name == "orders"


def test_domain_and_kind_filters_narrow_facet(env) -> None:
    res = _search(env, "orders", domain="sql")
    assert res.domain_items
    assert all(h.domain == "sql" for h in res.domain_items)
    res = _search(env, "orders", kind="data_table")
    assert {h.qualified_name for h in res.domain_items} == {"orders"}
    res = _search(env, "orders", kind="function")
    assert res.domain_items == ()


def test_language_filter_skips_domain_facet_honestly(env) -> None:
    res = _search(env, "orders", language="python")
    assert res.domain_items == ()
    assert any(d.code == "domain-facet-skipped"
               for d in res.diagnostics)
    # hits de símbolo continuam funcionando com o filtro
    assert all(h.language == "python" for h in res.items)


def test_via_relation_hit_declares_real_edge(env) -> None:
    res = _search(env, "orders")
    related = [h for h in res.domain_items
               if h.matched_fields == ("via_relation",)]
    assert related, "transformação que lê mart.orders deveria aparecer"
    for h in related:
        assert any(e.startswith("via=") and ":rel:" in e
                   for e in h.evidence)
        assert h.score < 1.0  # adjacência nunca supera match direto


def test_domain_total_and_determinism(env) -> None:
    res = _search(env, "orders")
    assert res.domain_total >= len(res.domain_items)
    again = _search(env, "orders")
    assert [(h.entity_id, h.qualified_name) for h in res.domain_items] == \
           [(h.entity_id, h.qualified_name) for h in again.domain_items]


def test_items_carry_scope_tags(env) -> None:
    res = _search(env, "process_orders", strategy="hybrid")
    assert res.items
    assert all(h.repository_id is not None
               and h.snapshot_id == res.snapshot_id for h in res.items)


def test_unknown_domain_returns_empty_not_error(env) -> None:
    res = _search(env, "orders", domain="nonexistent")
    assert res.domain_items == ()
    assert res.domain_total == 0
