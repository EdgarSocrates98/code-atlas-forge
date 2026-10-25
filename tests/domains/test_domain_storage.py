"""Persistência de domínio — schema v9, Store, persist_domain_result.

Cobre: migração aditiva (DB antigo abre), dedup por identity_key
(homônimos em catálogos distintos não colidem), CHECK constraints,
isolamento por snapshot (cascade + auditoria cross-snapshot) e a ponte
código↔domínio via ``*_symbol_occ_id``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from code_atlas.contracts.enums import ConfidenceClass, ResolutionStatus
from code_atlas.domains import (
    DomainEntity,
    DomainEntityKind,
    DomainExtractionResult,
    DomainIdentity,
    DomainResolution,
    ExtractedDomainEntity,
    ExtractedDomainRelation,
)
from code_atlas.domains.evidence import evidence_record
from code_atlas.indexing import IndexCoordinator
from code_atlas.indexing.persist import persist_domain_result
from code_atlas.storage import connect, migrate, schema_version
from code_atlas.storage import migrations as mig
from code_atlas.storage.store import Store

REPO_KEY = "git-remote:git@example/warehouse"


def _ident(**kw: object) -> DomainIdentity:
    base: dict[str, object] = {
        "domain": "sql",
        "repository": REPO_KEY,
        "qualified_name": "analytics.orders",
    }
    base.update(kw)
    return DomainIdentity(**base)  # type: ignore[arg-type]


@pytest.fixture()
def workspace(tmp_path: Path) -> tuple[sqlite3.Connection, int, int, dict[str, int]]:
    """Corpus real indexado: repo + snapshot READY + inventário.

    Retorna ``(conn, repo_id, snapshot_id, file_ids)`` com ``q.sql`` (sem
    parser — inventário puro) e ``mod.py`` (símbolo ``mod.target`` real).
    """
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "q.sql").write_text("SELECT * FROM analytics.orders\n")
    (root / "mod.py").write_text("def target():\n    return 1\n")
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    out = IndexCoordinator(conn).index(root)
    assert out.status == "success" and out.snapshot_id is not None
    store = Store(conn)
    snap = store.snapshot_by_id(out.snapshot_id)
    assert snap is not None
    file_ids = {
        r["relative_path"]: int(r["id"])
        for r in conn.execute(
            "SELECT id, relative_path FROM source_files"
            " WHERE repository_id = ?",
            (snap.repository_id,),
        )
    }
    return conn, snap.repository_id, out.snapshot_id, file_ids


def test_migration_v9_creates_domain_tables(db: sqlite3.Connection) -> None:
    assert schema_version(db) == 9
    tables = {
        r["name"]
        for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {
        "domain_entities", "domain_entity_occurrences", "domain_relations"
    } <= tables


def test_old_db_opens_and_gains_domain_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Banco do schema v8 abre e recebe v9 aditivamente — nada é quebrado."""
    conn = connect(tmp_path / "old.db")
    real = mig.migrations
    monkeypatch.setattr(
        mig, "migrations",
        lambda *, fts5: [m for m in real(fts5=fts5) if m.version <= 8],
    )
    assert mig.migrate(conn) == 8
    monkeypatch.undo()
    conn.close()

    conn2 = connect(tmp_path / "old.db")
    assert mig.migrate(conn2) == 9
    row = conn2.execute(
        "SELECT COUNT(*) AS c FROM domain_entities"
    ).fetchone()
    assert row["c"] == 0


def test_same_name_different_catalog_stored_as_two_entities(
    workspace: tuple[sqlite3.Connection, int, int, dict[str, int]],
) -> None:
    conn, repo_id, _snap, _fids = workspace
    store = Store(conn)
    a = DomainEntity(
        identity=_ident(catalog="glue:111:us-east-1"),
        kind=DomainEntityKind.DATA_TABLE, name="orders",
    )
    b = DomainEntity(
        identity=_ident(catalog="glue:222:eu-west-1"),
        kind=DomainEntityKind.DATA_TABLE, name="orders",
    )
    ids = store.ensure_domain_entities(repo_id, [a, b])
    assert len(ids) == 2
    assert ids[a.identity_key()] != ids[b.identity_key()]
    # dedup estável: repetir não cria linhas novas
    again = store.ensure_domain_entities(repo_id, [a, b])
    assert again == ids


def test_domain_relation_check_rejects_unknown_type(
    workspace: tuple[sqlite3.Connection, int, int, dict[str, int]],
) -> None:
    conn, repo_id, snap, _fids = workspace
    store = Store(conn)
    ids = store.ensure_domain_entities(
        repo_id,
        [DomainEntity(identity=_ident(), kind="data_table", name="orders")],
    )
    occ = store.insert_domain_occurrences_batch(
        [(snap, ids[_ident().logical_key()], None,
          None, None, None, None, None, "{}", "extracted")]
    )[0]
    with pytest.raises(sqlite3.IntegrityError):
        store.insert_domain_relations(
            [(snap, occ, None, occ, None, None, "MAKES_COFFEE",
              "resolved", "syntactic", "domain:sql", "[]")]
        )


def test_domain_relation_requires_some_target(
    workspace: tuple[sqlite3.Connection, int, int, dict[str, int]],
) -> None:
    conn, repo_id, snap, _fids = workspace
    store = Store(conn)
    ids = store.ensure_domain_entities(
        repo_id,
        [DomainEntity(identity=_ident(), kind="data_table", name="orders")],
    )
    occ = store.insert_domain_occurrences_batch(
        [(snap, ids[_ident().logical_key()], None,
          None, None, None, None, None, "{}", "extracted")]
    )[0]
    with pytest.raises(sqlite3.IntegrityError):
        store.insert_domain_relations(
            [(snap, occ, None, None, None, None, "READS_FROM",
              "unresolved", "syntactic", "domain:sql", "[]")]
        )


def test_persist_domain_result_end_to_end(
    workspace: tuple[sqlite3.Connection, int, int, dict[str, int]],
) -> None:
    conn, repo_id, snap, file_ids = workspace
    store = Store(conn)
    sym_occs = store.symbol_occurrences_for_name(snap, "mod.target")
    assert len(sym_occs) == 1  # função real indexada

    job = _ident(qualified_name="jobs.daily_load")
    table_a = _ident(catalog="glue:111:us-east-1")
    table_b = _ident(catalog="glue:222:eu-west-1")  # homônimo outro catálogo
    ext = _ident(qualified_name="ext.vault",
                 catalog="aws:999:us-east-1")
    result = DomainExtractionResult(
        domain_id="sql",
        adapter_version="0.1.0",
        entities=(
            ExtractedDomainEntity(
                identity=job, kind=DomainEntityKind.DATA_PIPELINE,
                name="daily_load", path="q.sql",
            ),
            ExtractedDomainEntity(
                identity=table_a, kind=DomainEntityKind.DATA_TABLE,
                name="orders", path="q.sql",
            ),
            ExtractedDomainEntity(
                identity=table_b, kind=DomainEntityKind.DATA_TABLE,
                name="orders", path="q.sql",
            ),
        ),
        relations=(
            # resolvida: alvo existe no snapshot
            ExtractedDomainRelation(
                relation_type="READS_FROM", path="q.sql",
                from_identity=job, to_identity=table_a,
            ),
            # external: identidade conhecida fora do snapshot
            ExtractedDomainRelation(
                relation_type="READS_FROM", path="q.sql",
                from_identity=job, to_identity=ext,
                resolution=DomainResolution(
                    status=ResolutionStatus.EXTERNAL,
                    confidence_class=ConfidenceClass.HEURISTIC,
                    resolver="domain:sql",
                ),
            ),
            # ponte domínio→código: origem entidade, alvo símbolo
            ExtractedDomainRelation(
                relation_type="REFERENCES", path="q.sql",
                from_identity=job, to_symbol="mod.target",
            ),
            # alvo textual honesto
            ExtractedDomainRelation(
                relation_type="WRITES_TO", path="q.sql",
                from_identity=job, to_unresolved="s3://bucket/out",
            ),
            # origem símbolo → domínio (PySpark lê tabela)
            ExtractedDomainRelation(
                relation_type="READS_FROM", path="q.sql",
                from_symbol="mod.target", to_identity=table_b,
            ),
        ),
        evidence=(
            evidence_record(
                adapter_domain="sql", adapter_version="0.1.0",
                path="q.sql", content_hash="sha256:test",
            ),
        ),
    )
    counts = persist_domain_result(store, repo_id, snap, file_ids, result)
    assert counts["entities"] == 3
    assert counts["occurrences"] == 3
    assert counts["relations"] == 5
    assert counts["evidence"] == 1
    assert counts["edges_skipped"] == 0

    rows = conn.execute(
        "SELECT type, resolution_status, to_entity_occ_id, "
        "to_symbol_occ_id, to_unresolved, confidence_class "
        "FROM domain_relations ORDER BY id"
    ).fetchall()
    statuses = [r["resolution_status"] for r in rows]
    assert statuses == [
        "resolved", "external", "resolved", "unresolved", "resolved",
    ]
    # alvo resolved ligou ao occurrence correto (catálogo A, não B)
    ent_a = store.domain_entity_id_for(repo_id, table_a.logical_key())
    occ_a = store.domain_occurrences_for(snap, ent_a)  # type: ignore[arg-type]
    assert rows[0]["to_entity_occ_id"] == occ_a[0]
    # ponte para símbolo de código
    assert rows[2]["to_symbol_occ_id"] == sym_occs[0]
    # origem símbolo → entidade do catálogo B (homônimo correto)
    assert rows[4]["to_entity_occ_id"] is not None
    ent_b = store.domain_entity_id_for(repo_id, table_b.logical_key())
    occ_b = store.domain_occurrences_for(snap, ent_b)  # type: ignore[arg-type]
    assert rows[4]["to_entity_occ_id"] == occ_b[0]
    # auditoria cross-snapshot limpa
    assert store.cross_snapshot_domain_relations(snap) == []


def test_ambiguous_symbol_target_stays_unresolved(
    workspace: tuple[sqlite3.Connection, int, int, dict[str, int]],
) -> None:
    """Símbolo com >1 ocorrência → alvo fica textual+ambiguous, nunca
    resolved-por-sorte."""
    conn, repo_id, snap, file_ids = workspace
    store = Store(conn)
    # segunda ocorrência do mesmo símbolo no snapshot (mesmo símbolo
    # lógico, outro arquivo) — qname "mod.target" em outro arquivo
    other_fid = file_ids["q.sql"]
    sym_id = conn.execute(
        "SELECT logical_symbol_id FROM symbol_occurrences WHERE id = ?",
        (store.symbol_occurrences_for_name(snap, "mod.target")[0],),
    ).fetchone()["logical_symbol_id"]
    store.insert_occurrences_batch(
        [(snap, sym_id, other_fid, 0, 10, 1, 0, None, "parsed")]
    )
    src = _ident(qualified_name="jobs.daily_load")
    result = DomainExtractionResult(
        domain_id="sql", adapter_version="0.1.0",
        entities=(
            ExtractedDomainEntity(
                identity=src, kind=DomainEntityKind.DATA_PIPELINE,
                name="daily_load", path="q.sql",
            ),
        ),
        relations=(
            ExtractedDomainRelation(
                relation_type="REFERENCES", path="q.sql",
                from_identity=src, to_symbol="mod.target",
            ),
        ),
    )
    counts = persist_domain_result(store, repo_id, snap, file_ids, result)
    assert counts["relations"] == 1
    row = conn.execute(
        "SELECT resolution_status, to_unresolved, to_symbol_occ_id "
        "FROM domain_relations"
    ).fetchone()
    assert row["resolution_status"] == "ambiguous"
    assert row["to_unresolved"] == "mod.target"
    assert row["to_symbol_occ_id"] is None


def test_relation_without_anchor_source_is_skipped(
    workspace: tuple[sqlite3.Connection, int, int, dict[str, int]],
) -> None:
    conn, repo_id, snap, file_ids = workspace
    store = Store(conn)
    ghost = _ident(qualified_name="jobs.ghost")  # nunca extraído
    result = DomainExtractionResult(
        domain_id="sql", adapter_version="0.1.0",
        relations=(
            ExtractedDomainRelation(
                relation_type="READS_FROM", path="q.sql",
                from_identity=ghost, to_unresolved="x",
            ),
        ),
    )
    counts = persist_domain_result(store, repo_id, snap, file_ids, result)
    assert counts["relations"] == 0
    assert counts["edges_skipped"] == 1
    n = conn.execute("SELECT COUNT(*) AS c FROM domain_relations").fetchone()
    assert n["c"] == 0


def test_inferred_resolved_rejected_at_persist(
    workspace: tuple[sqlite3.Connection, int, int, dict[str, int]],
) -> None:
    conn, repo_id, snap, file_ids = workspace
    store = Store(conn)
    src = _ident(qualified_name="jobs.x")
    dst = _ident(qualified_name="jobs.y")
    result = DomainExtractionResult(
        domain_id="sql", adapter_version="0.1.0",
        entities=(
            ExtractedDomainEntity(
                identity=src, kind=DomainEntityKind.DATA_PIPELINE,
                name="x", path="q.sql",
            ),
            ExtractedDomainEntity(
                identity=dst, kind=DomainEntityKind.DATA_TABLE,
                name="y", path="q.sql",
            ),
        ),
        relations=(
            ExtractedDomainRelation(
                relation_type="READS_FROM", path="q.sql",
                from_identity=src, to_identity=dst,
                resolution=DomainResolution(
                    status=ResolutionStatus.UNRESOLVED,
                    confidence_class=ConfidenceClass.INFERRED,
                    resolver="domain:sql",
                ),
            ),
        ),
    )
    # o alvo vincula → status sobe para resolved; inferred+resolved é
    # violação de contrato e deve falhar alto, não persistir mentira.
    with pytest.raises(ValueError, match="inferred"):
        persist_domain_result(store, repo_id, snap, file_ids, result)


def test_snapshot_delete_cascades_domain_rows(
    workspace: tuple[sqlite3.Connection, int, int, dict[str, int]],
) -> None:
    conn, repo_id, snap, file_ids = workspace
    store = Store(conn)
    e = _ident()
    result = DomainExtractionResult(
        domain_id="sql", adapter_version="0.1.0",
        entities=(
            ExtractedDomainEntity(
                identity=e, kind=DomainEntityKind.DATA_TABLE,
                name="orders", path="q.sql",
            ),
        ),
        relations=(
            ExtractedDomainRelation(
                relation_type="READS_FROM", path="q.sql",
                from_identity=e, to_unresolved="elsewhere",
            ),
        ),
    )
    persist_domain_result(store, repo_id, snap, file_ids, result)
    # index_jobs.snapshot_id não tem cascade — mesmo desfazimento do GC
    conn.execute("UPDATE index_jobs SET snapshot_id = NULL WHERE snapshot_id = ?",
                 (snap,))
    conn.execute("DELETE FROM snapshots WHERE id = ?", (snap,))
    assert conn.execute(
        "SELECT COUNT(*) AS c FROM domain_entity_occurrences"
    ).fetchone()["c"] == 0
    assert conn.execute(
        "SELECT COUNT(*) AS c FROM domain_relations"
    ).fetchone()["c"] == 0
    # entidade lógica sobrevive (identidade por repositório, não snapshot)
    assert conn.execute(
        "SELECT COUNT(*) AS c FROM domain_entities"
    ).fetchone()["c"] == 1


def test_snapshot_domain_entities_query(
    workspace: tuple[sqlite3.Connection, int, int, dict[str, int]],
) -> None:
    conn, repo_id, snap, file_ids = workspace
    store = Store(conn)
    result = DomainExtractionResult(
        domain_id="sql", adapter_version="0.1.0",
        entities=(
            ExtractedDomainEntity(
                identity=_ident(catalog="catA"),
                kind=DomainEntityKind.DATA_TABLE, name="orders",
                path="q.sql",
            ),
            ExtractedDomainEntity(
                identity=_ident(catalog="catB"),
                kind=DomainEntityKind.DATA_TABLE, name="orders",
                path="q.sql",
            ),
        ),
    )
    persist_domain_result(store, repo_id, snap, file_ids, result)
    rows = store.snapshot_domain_entities(snap)
    assert len(rows) == 2
    assert {r[2] for r in rows} == {"analytics.orders"}
    assert {r[4] for r in rows} == {"q.sql"}
    assert len({r[3] for r in rows}) == 2  # identity_keys distintas
