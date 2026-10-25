"""Round-trip ``persist_domain_result`` + hook opt-in do coordinator.

Dois níveis, como o contrato pede:

- ``persist_domain_result`` direto — extrai de ``pyspark_repo`` e
  persiste contra um snapshot que inventariou os ``.py`` como
  ``source_files``;
- ``IndexCoordinator(domain_registry=…)`` — o hook opt-in roda o
  adapter durante ``index()`` e persiste na mesma transação. Sem
  registry o comportamento é byte a byte o anterior (zero rows de
  domínio).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from conftest import FIXTURE, make_request

from code_atlas.domains.registry import DomainRegistry
from code_atlas.domains.spark import PySparkAdapter
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
    res = PySparkAdapter(base_path=FIXTURE).extract(make_request())
    counts = persist_domain_result(Store(conn), repo_id, snap, file_ids, res)
    assert counts["entities"] > 0
    assert counts["occurrences"] > 0
    assert counts["relations"] > 0
    assert counts["evidence"] > 0

    edges = _rel_edges(conn)
    # pipeline staging → mart resolvido contra ocorrências do snapshot
    assert (
        "mart.enriched_orders", "DERIVES_FROM",
        "staging.orders", "resolved",
    ) in edges
    assert (
        "mart.enriched_orders", "DERIVES_FROM",
        "s3://datalake/customers/", "resolved",
    ) in edges
    # write → sink resolvido
    assert (
        "etl_orders.py#enriched@24", "WRITES_TO",
        "mart.enriched_orders", "resolved",
    ) in edges
    # iceberg-style write persistido
    assert (
        "sql_job.py#region_counts@11", "WRITES_TO",
        "mart.customer_region_counts", "resolved",
    ) in edges


def test_unresolved_relation_persisted_honestly(
    workspace: tuple[sqlite3.Connection, int, int, dict[str, int]],
) -> None:
    conn, repo_id, snap, file_ids = workspace
    res = PySparkAdapter(base_path=FIXTURE).extract(make_request())
    persist_domain_result(Store(conn), repo_id, snap, file_ids, res)
    rows = conn.execute(
        "SELECT resolution_status, to_unresolved FROM domain_relations "
        "WHERE to_entity_occ_id IS NULL"
    ).fetchall()
    assert rows, "expected unresolved relations"
    for r in rows:
        assert r["resolution_status"] == "unresolved"
        assert r["to_unresolved"]


def test_snapshot_isolation_and_dedup(
    workspace: tuple[sqlite3.Connection, int, int, dict[str, int]],
) -> None:
    conn, repo_id, snap, file_ids = workspace
    res = PySparkAdapter(base_path=FIXTURE).extract(make_request())
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
    reg.register(PySparkAdapter(base_path=FIXTURE))
    out = IndexCoordinator(conn, domain_registry=reg).index(FIXTURE)
    assert out.status == "success"
    assert out.stats["domain_adapters"] == 1
    conn.close()
