"""Round-trip ``persist_domain_result`` + hook opt-in do coordinator.

Dois níveis, como o contrato pede:

- ``persist_domain_result`` direto — extrai da fixture real e persiste
  contra um snapshot que inventariou os ``.sql`` como ``source_files``;
- ``IndexCoordinator(domain_registry=…)`` — o hook opt-in roda o
  adapter durante ``index()`` e persiste na mesma transação. Sem
  registry o comportamento é byte a byte o anterior (zero rows de
  domínio), garantido por teste.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from conftest import FIXTURE, make_request

from code_atlas.domains.registry import DomainRegistry
from code_atlas.domains.sql import SqlDomainAdapter
from code_atlas.indexing import IndexCoordinator
from code_atlas.indexing.persist import persist_domain_result
from code_atlas.storage import connect, migrate
from code_atlas.storage.store import Store


@pytest.fixture()
def workspace(
    tmp_path: Path,
) -> tuple[sqlite3.Connection, int, int, dict[str, int]]:
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    out = IndexCoordinator(conn).index(FIXTURE)
    assert out.status == "success" and out.snapshot_id is not None
    snap = Store(conn).snapshot_by_id(out.snapshot_id)
    assert snap is not None
    file_ids = {
        r["relative_path"]: int(r["id"])
        for r in conn.execute(
            "SELECT id, relative_path FROM source_files"
            " WHERE repository_id = ?",
            (snap.repository_id,),
        )
    }
    yield conn, snap.repository_id, out.snapshot_id, file_ids
    conn.close()


def _rel_edges(conn: sqlite3.Connection) -> set[tuple[str, str, str, str]]:
    rows = conn.execute(
        "SELECT de.namespace AS fns, de.qualified_name AS fqn, "
        "       dr.type AS relation_type, "
        "       de2.namespace AS tns, de2.qualified_name AS tgt, "
        "       dr.to_unresolved, dr.resolution_status "
        "FROM domain_relations dr "
        "JOIN domain_entity_occurrences fo "
        "  ON fo.id = dr.from_entity_occ_id "
        "JOIN domain_entities de ON de.id = fo.entity_id "
        "LEFT JOIN domain_entity_occurrences to2 "
        "  ON to2.id = dr.to_entity_occ_id "
        "LEFT JOIN domain_entities de2 ON de2.id = to2.entity_id"
    ).fetchall()

    def _q(ns: str, name: str | None) -> str | None:
        if name is None:
            return None
        return f"{ns}.{name}" if ns else name

    return {
        (_q(r["fns"], r["fqn"]), r["relation_type"],
         _q(r["tns"], r["tgt"]) or r["to_unresolved"],
         r["resolution_status"])
        for r in rows
    }


def test_persist_domain_result_end_to_end(
    workspace: tuple[sqlite3.Connection, int, int, dict[str, int]],
) -> None:
    conn, repo_id, snap, file_ids = workspace
    res = SqlDomainAdapter(base_path=FIXTURE).extract(make_request())
    counts = persist_domain_result(Store(conn), repo_id, snap, file_ids, res)
    assert counts["entities"] > 0
    assert counts["occurrences"] > 0
    assert counts["relations"] > 0
    assert counts["evidence"] > 0

    edges = _rel_edges(conn)
    # pipeline landing → staging → mart resolvido contra ocorrências
    assert (
        "staging.v_enriched_orders", "DERIVES_FROM",
        "landing.orders", "resolved",
    ) in edges
    assert (
        "staging.order_facts", "DERIVES_FROM",
        "staging.v_enriched_orders", "resolved",
    ) in edges
    assert (
        "mart.daily_revenue", "DERIVES_FROM",
        "staging.order_facts", "resolved",
    ) in edges
    # linhagem de coluna persistida como aresta entre entidades
    assert (
        "staging.order_facts.amount_usd", "DERIVES_FROM",
        "staging.v_enriched_orders.amount_usd", "resolved",
    ) in edges


def test_snapshot_isolation_and_dedup(
    workspace: tuple[sqlite3.Connection, int, int, dict[str, int]],
) -> None:
    conn, repo_id, snap, file_ids = workspace
    res = SqlDomainAdapter(base_path=FIXTURE).extract(make_request())
    store = Store(conn)
    persist_domain_result(store, repo_id, snap, file_ids, res)
    c1 = conn.execute(
        "SELECT COUNT(*) AS c FROM domain_entities"
    ).fetchone()["c"]
    persist_domain_result(store, repo_id, snap, file_ids, res)
    c2 = conn.execute(
        "SELECT COUNT(*) AS c FROM domain_entities"
    ).fetchone()["c"]
    assert c1 == c2


def test_coordinator_without_registry_persists_no_domain_rows(
    workspace: tuple[sqlite3.Connection, int, int, dict[str, int]],
) -> None:
    # default None — o hook é opt-in; o índice de código não toca
    # nas tabelas de domínio.
    conn, _repo_id, _snap, _file_ids = workspace
    for table in (
        "domain_entities", "domain_entity_occurrences",
        "domain_relations",
    ):
        n = conn.execute(
            f"SELECT COUNT(*) AS c FROM {table}"
        ).fetchone()["c"]
        assert n == 0, table


def test_coordinator_with_registry_persists_pipeline(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    reg = DomainRegistry()
    reg.register(SqlDomainAdapter(base_path=FIXTURE))
    out = IndexCoordinator(conn, domain_registry=reg).index(FIXTURE)
    assert out.status == "success"
    assert out.stats["domain_adapters"] == 1
    # broken/99_broken.sql é malformado por desenho — seus parse-error
    # viram failures auditáveis no job, sem impedir a persistência
    assert out.stats["domain_failures"] == 2
    job = conn.execute(
        "SELECT failures FROM index_jobs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert job is not None
    assert "broken/99_broken.sql:domain-parse-error" in job["failures"]
    assert out.stats["domain_sql_entities"] > 0
    assert out.stats["domain_sql_relations"] > 0
    edges = _rel_edges(conn)
    assert (
        "mart.customer_stats", "DERIVES_FROM",
        "staging.order_facts", "resolved",
    ) in edges
    assert (
        "mart.daily_revenue", "DERIVES_FROM",
        "staging.order_facts", "resolved",
    ) in edges
    conn.close()


def test_coordinator_registry_changes_analyzer_versions(
    tmp_path: Path,
) -> None:
    # habilitar o domínio derrota o no-op — como um parser novo
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    first = IndexCoordinator(conn).index(FIXTURE)
    assert first.status == "success"
    reg = DomainRegistry()
    reg.register(SqlDomainAdapter(base_path=FIXTURE))
    second = IndexCoordinator(conn, domain_registry=reg).index(FIXTURE)
    assert second.status == "success"
    assert second.snapshot_id != first.snapshot_id
    conn.close()
