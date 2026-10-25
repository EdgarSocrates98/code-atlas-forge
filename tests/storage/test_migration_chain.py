"""lf-0155 (Cycle 8.1 Phase 13) — cadeia de migrações com preservação.

Um banco v1 **real** (produzido pelo SQL de v1 do catálogo — checksum
idêntico ao que um build Cycle-1 gravaria) com dados migra até o head
(v12 com FTS5 / v11 sem) sem perda: linhas e campos intactos, backfills
de v5/v6 aplicados, tabelas de domínio/federação/vetores criadas. O
trecho v9→v12 é exercitado com dados de domínio de verdade: ``ANALYZE``
da v11 popula ``sqlite_stat1`` e ``domain_fts`` nasce vazia para
snapshots pré-v12 (persist só cobre novos) — ``Store.rebuild_domain_fts``
é o backfill honesto.

Fail-safe (GA): ``schema_migrations`` com versão fora do catálogo
completo = banco de build mais nova → ``MigrationError``; checksum
drift em versão conhecida → ``MigrationError``. Um banco v12 aberto
por build sem FTS5 é caminho projetado (v12 está no catálogo — LIKE
fallback), não versão desconhecida.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from code_atlas.storage import migrations as mig
from code_atlas.storage.db import connect, fts5_available
from code_atlas.storage.store import Store

_DB = "ws/atlas.db"


def _fresh(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / _DB)


def _head(conn: sqlite3.Connection) -> int:
    return 12 if fts5_available(conn) else 11


def _seed_v1(conn: sqlite3.Connection) -> None:
    """Linhas representativas usando SÓ colunas do schema v1."""
    conn.execute(
        "INSERT INTO repositories (origin, root_handle, git_provider,"
        " default_branch) VALUES ('file','/ws/repo','git','main')")
    conn.execute(
        "INSERT INTO snapshots (repository_id, git_ref, commit_sha,"
        " worktree_digest, analyzer_versions, status) VALUES"
        " (1,'main','abc123','digest1','{\"a\":\"1\"}','READY')")
    conn.execute(
        "INSERT INTO source_files (repository_id, relative_path,"
        " language, sha256, size_bytes) VALUES"
        " (1,'src/a.py','python','h1',42)")
    conn.execute(
        "INSERT INTO file_inventory (snapshot_id, source_file_id, sha256)"
        " VALUES (1,1,'h1')")
    conn.execute(
        "INSERT INTO symbols (repository_id, language, qualified_name,"
        " kind) VALUES (1,'python','mod.func','function')")
    conn.execute(
        "INSERT INTO symbol_occurrences (snapshot_id, logical_symbol_id,"
        " source_file_id, span_start, span_end, line, col, content_hash)"
        " VALUES (1,1,1,0,10,1,0,'ch1')")
    conn.execute(
        "INSERT INTO relations (snapshot_id, from_id, to_id, type,"
        " resolution_status, confidence_class, resolver, evidence_ids)"
        " VALUES (1,1,1,'CALLS','resolved','resolved','r1','[1]')")
    conn.execute(
        "INSERT INTO evidence (snapshot_id, source_file_id, path, span,"
        " content_hash, extractor, extractor_version, notes) VALUES"
        " (1,1,'src/a.py','1:0-10','ch1','tree-sitter','1.0','n')")
    conn.execute(
        "INSERT INTO index_jobs (repository_id, snapshot_id, status,"
        " counts) VALUES (1,1,'success','{\"files\":1}')")
    conn.commit()


def _seed_v9(conn: sqlite3.Connection) -> None:
    """Dados de domínio no estado v9 (pré-v11 índices, pré-v12 FTS)."""
    conn.execute(
        "INSERT INTO repositories (origin, root_handle, identity_key)"
        " VALUES ('file','/ws/dwh','path:/ws/dwh')")
    conn.execute(
        "INSERT INTO snapshots (repository_id, git_ref, worktree_digest,"
        " status) VALUES (1,'main','d9','READY')")
    conn.execute(
        "INSERT INTO source_files (repository_id, relative_path, language)"
        " VALUES (1,'models/marts.sql','sql')")
    conn.execute(
        "INSERT INTO domain_entities (repository_id, domain, kind,"
        " identity_key, qualified_name, name, catalog, namespace) VALUES"
        " (1,'sql','table','sql:table:mart.orders','mart.orders','orders',"
        " 'cat','mart')")
    conn.execute(
        "INSERT INTO domain_entities (repository_id, domain, kind,"
        " identity_key, qualified_name, name) VALUES"
        " (1,'sql','view','sql:view:mart.v_orders','mart.v_orders','v_orders')")
    conn.execute(
        "INSERT INTO domain_entity_occurrences (snapshot_id, entity_id,"
        " source_file_id, span_start, span_end, line, col, content_hash)"
        " VALUES (1,1,1,0,40,1,0,'c1'),(1,2,1,41,80,1,41,'c2')")
    conn.execute(
        "INSERT INTO domain_relations (snapshot_id, from_entity_occ_id,"
        " to_entity_occ_id, type, resolution_status, confidence_class,"
        " resolver) VALUES"
        " (1,2,1,'READS_FROM','resolved','resolved','sql-dom')")
    conn.commit()


def _apply_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, up_to: int
) -> sqlite3.Connection:
    """Banco genuíno no estado ``up_to`` — SQL real do catálogo."""
    real = mig.migrations
    monkeypatch.setattr(
        mig, "migrations",
        lambda *, fts5: [m for m in real(fts5=fts5) if m.version <= up_to],
    )
    conn = _fresh(tmp_path)
    assert mig.migrate(conn) == up_to
    monkeypatch.undo()
    return conn


def test_v1_data_survives_full_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _apply_only(tmp_path, monkeypatch, 1)
    _seed_v1(conn)
    conn.close()

    conn = connect(tmp_path / _DB)
    head = _head(conn)
    assert mig.migrate(conn) == head
    assert mig.migrate(conn) == head  # idempotente

    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    # dados v1 intactos
    assert q("SELECT root_handle FROM repositories") == "/ws/repo"
    assert q("SELECT worktree_digest FROM snapshots") == "digest1"
    assert q("SELECT qualified_name FROM symbols") == "mod.func"
    assert q("SELECT type FROM relations") == "CALLS"
    assert q("SELECT extractor FROM evidence") == "tree-sitter"
    assert q("SELECT status FROM index_jobs") == "success"
    # backfills de v5/v6 aplicados aos dados antigos
    assert q("SELECT identity_key FROM repositories") == "path:/ws/repo"
    assert q("SELECT dirty FROM snapshots") == 0
    assert q("SELECT resolver_id FROM evidence") == ""
    # objetos de cada geração existem
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','index')"
        " OR type='table'")}
    assert {"domain_entities", "domain_entity_occurrences",
            "domain_relations", "federation_workspaces",
            "federation_members", "vector_embeddings",
            "parse_result_cache"} <= names
    if fts5_available(conn):
        assert "domain_fts" in names
        assert "fts_symbols" in names and "fts_occurrences" in names


def test_v9_domain_data_survives_v10_v11_v12(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _apply_only(tmp_path, monkeypatch, 9)
    _seed_v9(conn)
    conn.close()

    conn = connect(tmp_path / _DB)
    assert mig.migrate(conn) == _head(conn)

    # dados de domínio íntegros através de v10/v11/v12
    rows = conn.execute(
        "SELECT domain, kind, qualified_name, identity_key"
        " FROM domain_entities ORDER BY id").fetchall()
    assert [(r[0], r[1], r[2]) for r in rows] == [
        ("sql", "table", "mart.orders"),
        ("sql", "view", "mart.v_orders"),
    ]
    rel = conn.execute(
        "SELECT from_entity_occ_id, to_entity_occ_id, type"
        " FROM domain_relations").fetchone()
    assert tuple(rel) == (2, 1, "READS_FROM")
    # v11: índices compostos + stats de ANALYZE no banco já povoado
    idx = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    assert {
        "idx_domain_relations_snap_from_ent",
        "idx_domain_relations_snap_to_ent",
        "idx_domain_relations_snap_from_sym",
        "idx_domain_relations_snap_to_sym",
    } <= idx
    stats = {r[0] for r in conn.execute(
        "SELECT tbl FROM sqlite_stat1")}
    assert {"domain_relations", "domain_entity_occurrences",
            "domain_entities"} <= stats
    # v10: federação criada vazia
    assert conn.execute(
        "SELECT COUNT(*) FROM federation_workspaces").fetchone()[0] == 0


@pytest.mark.skipif(
    not fts5_available(sqlite3.connect(":memory:")),
    reason="domain_fts é FTS5-gated (v12)")
def test_pre_v12_snapshot_backfills_domain_fts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Snapshot persistido antes da v12: FTS nasce vazio, rebuild cobre."""
    conn = _apply_only(tmp_path, monkeypatch, 9)
    _seed_v9(conn)
    conn.close()

    conn = connect(tmp_path / _DB)
    assert mig.migrate(conn) == 12
    # honestidade do desenho: persist cobre só snapshots novos — a
    # snapshot pré-v12 continua no LIKE bounded até o backfill.
    assert conn.execute(
        "SELECT COUNT(*) FROM domain_fts").fetchone()[0] == 0
    store = Store(conn)
    assert store.rebuild_domain_fts(snapshot_id=1) == 2
    hits = conn.execute(
        "SELECT entity_id FROM domain_fts"
        " WHERE domain_fts MATCH 'v_orders'").fetchall()
    assert {r[0] for r in hits} == {2}
    # idempotente dentro do mesmo escopo
    assert store.rebuild_domain_fts(snapshot_id=1) == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM domain_fts").fetchone()[0] == 2


def test_unknown_future_version_refused(tmp_path: Path) -> None:
    """GA fail-safe: banco de build mais nova nunca segue adiante."""
    conn = _fresh(tmp_path)
    head = mig.migrate(conn)
    conn.execute(
        "INSERT INTO schema_migrations (version, name, checksum)"
        " VALUES (99,'future-schema','deadbeef')")
    conn.commit()
    with pytest.raises(mig.MigrationError, match="unknown versions"):
        mig.migrate(conn)
    # nada foi aplicado nem corrompido — a recusa é o comportamento
    assert {r[0] for r in conn.execute(
        "SELECT version FROM schema_migrations")} == set(
        range(1, head + 1)) | {99}


def test_checksum_drift_on_known_version_refused(tmp_path: Path) -> None:
    conn = _fresh(tmp_path)
    mig.migrate(conn)
    conn.execute(
        "UPDATE schema_migrations SET checksum='tampered' WHERE version=3")
    conn.commit()
    with pytest.raises(mig.MigrationError, match="checksum drift"):
        mig.migrate(conn)


@pytest.mark.skipif(
    not fts5_available(sqlite3.connect(":memory:")),
    reason="precisa de v12 aplicada para simular o banco moderno")
def test_fts_gated_db_opens_on_nofts5_build(tmp_path: Path) -> None:
    """Banco v12 num build sem FTS5: projetado (LIKE), não desconhecido.

    As rows v2/v7/v12 são validadas por checksum contra o catálogo
    completo mesmo quando não aplicáveis nesta build.
    """
    conn = _fresh(tmp_path)
    assert mig.migrate(conn) == 12
    assert mig.migrate(conn, fts5=False) == 12
