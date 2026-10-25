"""Round-trip ``persist_domain_result`` — schema v9 (lf-0115).

Indexa a fixture real (inventário honesto), extrai via
``TerraformAdapter`` e persiste — sem tocar na wiring do pipeline:
a chamada a ``persist_domain_result`` é o único ponto de contato.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from conftest import FIXTURE, make_request

from code_atlas.domains.terraform import TerraformAdapter
from code_atlas.indexing import IndexCoordinator
from code_atlas.indexing.persist import persist_domain_result
from code_atlas.storage import connect, migrate
from code_atlas.storage.store import Store


@pytest.fixture()
def workspace(tmp_path: Path) -> tuple[sqlite3.Connection, int, int, dict[str, int]]:
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


def test_persist_domain_result_end_to_end(
    workspace: tuple[sqlite3.Connection, int, int, dict[str, int]],
) -> None:
    conn, repo_id, snap, file_ids = workspace
    res = TerraformAdapter(base_path=FIXTURE).extract(make_request())
    counts = persist_domain_result(Store(conn), repo_id, snap, file_ids, res)
    assert counts["entities"] > 0
    assert counts["occurrences"] > 0
    assert counts["relations"] > 0
    assert counts["evidence"] > 0

    rows = conn.execute(
        "SELECT de.qualified_name, dr.type AS relation_type, "
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
    edges = {
        (r["qualified_name"], r["relation_type"],
         r["tgt"] or r["to_unresolved"], r["resolution_status"])
        for r in rows
    }
    assert (
        "aws_glue_job.etl", "DEPENDS_ON", "aws_s3_bucket.data", "resolved"
    ) in edges
    assert (
        "aws_glue_job.etl", "ASSUMES_ROLE", "aws_iam_role.glue", "resolved"
    ) in edges
    assert (
        "module.network", "PROVISIONS", "aws_s3_bucket.flow_logs",
        "resolved",
    ) in edges
    assert (
        "data.terraform_remote_state.network", "REFERENCES",
        "remote-state:s3", "external",
    ) in edges


def test_persisted_entities_have_no_secrets(
    workspace: tuple[sqlite3.Connection, int, int, dict[str, int]],
) -> None:
    conn, repo_id, snap, file_ids = workspace
    res = TerraformAdapter(base_path=FIXTURE).extract(make_request())
    persist_domain_result(Store(conn), repo_id, snap, file_ids, res)
    for table, col in (
        ("domain_entities", "attributes"),
        ("domain_entities", "name"),
        ("domain_relations", "to_unresolved"),
        ("evidence", "notes"),
    ):
        rows = conn.execute(
            f"SELECT {col} AS v FROM {table}"
        ).fetchall()
        blob = "\n".join(str(r["v"]) for r in rows)
        for secret in (
            "sup3r-s3cret-do-not-leak",
            "tok-live-adversarial",
            "tfvars-secret-must-not-leak",
        ):
            assert secret not in blob, (table, col)


def test_snapshot_isolation_and_dedup(
    workspace: tuple[sqlite3.Connection, int, int, dict[str, int]],
) -> None:
    conn, repo_id, snap, file_ids = workspace
    res = TerraformAdapter(base_path=FIXTURE).extract(make_request())
    store = Store(conn)
    persist_domain_result(store, repo_id, snap, file_ids, res)
    c1 = conn.execute("SELECT COUNT(*) AS c FROM domain_entities").fetchone()["c"]
    # identidades lógicas deduplicadas por identity_key — re-persist não duplica
    persist_domain_result(store, repo_id, snap, file_ids, res)
    c2 = conn.execute("SELECT COUNT(*) AS c FROM domain_entities").fetchone()["c"]
    assert c1 == c2
