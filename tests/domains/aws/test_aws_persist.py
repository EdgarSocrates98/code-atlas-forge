"""Persistência de ``DomainExtractionResult`` aws-data (lf-0113).

Mesmo desenho de ``test_sql_persist``: snapshot real do fixture repo,
``persist_domain_result`` direto e dedup por identidade lógica. O hook
do coordinator é opt-in — sem registry, zero rows de domínio.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from conftest import FIXTURE, JOB_DEF, TRIGGER, make_request

from code_atlas.domains.aws import AwsDataAdapter
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
            "SELECT id, relative_path FROM source_files WHERE repository_id = ?",
            (snap.repository_id,),
        )
    }
    yield conn, snap.repository_id, out.snapshot_id, file_ids
    conn.close()


def _rel_edges(conn: sqlite3.Connection) -> set[tuple[str, str, str, str]]:
    rows = conn.execute(
        "SELECT de.catalog AS fcat, de.namespace AS fns, "
        "       de.qualified_name AS fqn, dr.type AS relation_type, "
        "       de2.qualified_name AS tgt, dr.to_unresolved, "
        "       dr.resolution_status "
        "FROM domain_relations dr "
        "JOIN domain_entity_occurrences fo "
        "  ON fo.id = dr.from_entity_occ_id "
        "JOIN domain_entities de ON de.id = fo.entity_id "
        "LEFT JOIN domain_entity_occurrences to2 "
        "  ON to2.id = dr.to_entity_occ_id "
        "LEFT JOIN domain_entities de2 ON de2.id = to2.entity_id"
    ).fetchall()

    def _q(cat: str, ns: str, name: str | None) -> str | None:
        if name is None:
            return None
        head = ".".join(p for p in (cat, ns) if p)
        return f"{head}.{name}" if head else name

    return {
        (
            _q(r["fcat"], r["fns"], r["fqn"]),
            r["relation_type"],
            _q("", "", r["tgt"]) or r["to_unresolved"],
            r["resolution_status"],
        )
        for r in rows
    }


def test_persist_domain_result_end_to_end(
    workspace: tuple[sqlite3.Connection, int, int, dict[str, int]],
) -> None:
    conn, repo_id, snap, file_ids = workspace
    res = AwsDataAdapter(base_path=FIXTURE).extract(make_request())
    counts = persist_domain_result(Store(conn), repo_id, snap, file_ids, res)
    assert counts["entities"] > 0
    assert counts["occurrences"] > 0
    assert counts["relations"] > 0
    assert counts["evidence"] > 0

    edges = _rel_edges(conn)
    # glue catalog read/write resolvido contra ocorrências do snapshot
    assert (
        "glue/jobs.orders_etl",
        "READS_FROM",
        "orders",
        "resolved",
    ) in edges or (
        "glue/jobs.orders_etl",
        "READS_FROM",
        "sales_db.orders",
        "resolved",
    ) in edges
    # ASSUMES_ROLE persistido; alvo é entidade do snapshot → resolved
    assert any(t == "ASSUMES_ROLE" for _f, t, _to, _st in edges)


def test_snapshot_isolation_and_dedup(
    workspace: tuple[sqlite3.Connection, int, int, dict[str, int]],
) -> None:
    conn, repo_id, snap, file_ids = workspace
    res = AwsDataAdapter(base_path=FIXTURE).extract(make_request())
    store = Store(conn)
    persist_domain_result(store, repo_id, snap, file_ids, res)
    c1 = conn.execute("SELECT COUNT(*) AS c FROM domain_entities").fetchone()["c"]
    persist_domain_result(store, repo_id, snap, file_ids, res)
    c2 = conn.execute("SELECT COUNT(*) AS c FROM domain_entities").fetchone()["c"]
    assert c1 == c2


def test_coordinator_without_registry_persists_no_domain_rows(
    workspace: tuple[sqlite3.Connection, int, int, dict[str, int]],
) -> None:
    conn, _repo_id, _snap, _file_ids = workspace
    for table in (
        "domain_entities",
        "domain_entity_occurrences",
        "domain_relations",
    ):
        n = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
        assert n == 0, table


def test_external_role_relation_persists(
    workspace: tuple[sqlite3.Connection, int, int, dict[str, int]],
) -> None:
    conn, repo_id, snap, file_ids = workspace
    res = AwsDataAdapter(base_path=FIXTURE).extract(make_request((JOB_DEF, TRIGGER)))
    persist_domain_result(Store(conn), repo_id, snap, file_ids, res)
    edges = _rel_edges(conn)
    # o role tem ocorrência no snapshot → endpoint vinculado → resolved;
    # o status adapter-level ``external`` vale no envelope, não na row
    assert any(
        frm == "jobs.nightly-etl" and t == "ASSUMES_ROLE" and st == "resolved"
        for frm, t, _to, st in edges
    )
    # condição externa (job fora do request) mantém alvo textual
    assert any(
        frm == "jobs.trigger:nightly-trigger"
        and t == "DEPENDS_ON"
        and to == "glue-job:upstream-loader"
        and st == "external"
        for frm, t, to, st in edges
    )
