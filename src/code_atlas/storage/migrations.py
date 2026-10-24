"""Runner de migrações versionadas (Blueprint §6).

Cada migração é ``(version, name, sql)``; aplicação é sequencial,
transacional e idempotente (versões aplicadas são puladas). O checksum do
SQL aplicado é gravado em ``schema_migrations`` e validado em execuções
posteriores — drift levanta ``MigrationError``.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass


class MigrationError(Exception):
    """Migração inválida ou drift de schema detectado."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


_SCHEMA_V1 = """
CREATE TABLE repositories (
    id INTEGER PRIMARY KEY,
    origin TEXT NOT NULL DEFAULT '',
    root_handle TEXT NOT NULL,
    git_provider TEXT NOT NULL DEFAULT '',
    default_branch TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE snapshots (
    id INTEGER PRIMARY KEY,
    repository_id INTEGER NOT NULL REFERENCES repositories(id),
    git_ref TEXT NOT NULL DEFAULT '',
    commit_sha TEXT,
    worktree_digest TEXT NOT NULL,
    analyzer_versions TEXT NOT NULL DEFAULT '{}',
    parent_snapshot_id INTEGER REFERENCES snapshots(id),
    status TEXT NOT NULL CHECK (status IN ('BUILDING','READY','FAILED','STALE')),
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE source_files (
    id INTEGER PRIMARY KEY,
    repository_id INTEGER NOT NULL REFERENCES repositories(id),
    relative_path TEXT NOT NULL,
    language TEXT NOT NULL,
    sha256 TEXT,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    ignored INTEGER NOT NULL DEFAULT 0,
    UNIQUE (repository_id, relative_path)
);

CREATE TABLE file_inventory (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    source_file_id INTEGER NOT NULL REFERENCES source_files(id),
    sha256 TEXT,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    ignored INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (snapshot_id, source_file_id)
);

CREATE TABLE symbols (
    id INTEGER PRIMARY KEY,
    repository_id INTEGER NOT NULL REFERENCES repositories(id),
    language TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    UNIQUE (repository_id, language, qualified_name, kind)
);

CREATE TABLE symbol_occurrences (
    id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    logical_symbol_id INTEGER NOT NULL REFERENCES symbols(id),
    source_file_id INTEGER NOT NULL REFERENCES source_files(id),
    span_start INTEGER,
    span_end INTEGER,
    line INTEGER,
    col INTEGER,
    content_hash TEXT,
    parse_status TEXT NOT NULL DEFAULT 'parsed'
);

CREATE TABLE relations (
    id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    from_id INTEGER NOT NULL,
    to_id INTEGER,
    to_unresolved TEXT,
    type TEXT NOT NULL CHECK (type IN (
        'CONTAINS','DEFINES','IMPORTS','CALLS','IMPLEMENTS',
        'EXTENDS','REFERENCES','CONFIGURES','TESTS')),
    resolution_status TEXT NOT NULL CHECK (resolution_status IN (
        'resolved','ambiguous','unresolved','external')),
    confidence_class TEXT NOT NULL CHECK (confidence_class IN (
        'resolved','syntactic','heuristic','inferred')),
    resolver TEXT NOT NULL,
    evidence_ids TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE evidence (
    id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    source_file_id INTEGER NOT NULL REFERENCES source_files(id),
    path TEXT NOT NULL,
    span TEXT,
    content_hash TEXT NOT NULL,
    extractor TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE index_jobs (
    id INTEGER PRIMARY KEY,
    repository_id INTEGER NOT NULL REFERENCES repositories(id),
    snapshot_id INTEGER REFERENCES snapshots(id),
    status TEXT NOT NULL CHECK (status IN ('running','success','failed','noop')),
    started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    finished_at TEXT,
    counts TEXT NOT NULL DEFAULT '{}',
    failures TEXT NOT NULL DEFAULT '[]',
    manifest_hash TEXT
);

CREATE INDEX idx_snapshots_repo_status ON snapshots (repository_id, status);
CREATE INDEX idx_file_inventory_snapshot ON file_inventory (snapshot_id);
CREATE INDEX idx_source_files_repo_path ON source_files (repository_id, relative_path);
CREATE INDEX idx_occurrences_snap_file ON symbol_occurrences (snapshot_id, source_file_id);
CREATE INDEX idx_relations_from ON relations (from_id, type);
CREATE INDEX idx_relations_to ON relations (to_id, type);
"""

_SCHEMA_V2_FTS = """
CREATE VIRTUAL TABLE fts_symbols USING fts5(
    qualified_name, language, kind, content=''
);
"""

_SCHEMA_V3_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_relations_snapshot ON relations (snapshot_id, type);
CREATE INDEX IF NOT EXISTS idx_occurrences_snapshot_symbol
    ON symbol_occurrences (snapshot_id, logical_symbol_id);
CREATE INDEX IF NOT EXISTS idx_evidence_snapshot ON evidence (snapshot_id);
"""

_SCHEMA_V4_CACHE = """
CREATE TABLE parse_result_cache (
    id INTEGER PRIMARY KEY,
    repository_id INTEGER NOT NULL REFERENCES repositories(id),
    path TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    language TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    grammar_version TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    resolver_version TEXT NOT NULL,
    builder_version TEXT NOT NULL,
    config_digest TEXT NOT NULL,
    payload TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    last_used_snapshot_id INTEGER,
    hit_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (repository_id, path, content_sha256, language,
            parser_version, grammar_version, extractor_version,
            resolver_version, builder_version, config_digest)
);
CREATE INDEX idx_cache_repo_path ON parse_result_cache (repository_id, path);
"""

_SCHEMA_V5_IDENTITY = """
ALTER TABLE repositories ADD COLUMN identity_key TEXT NOT NULL DEFAULT '';
UPDATE repositories SET identity_key = 'path:' || root_handle
 WHERE identity_key = '';
CREATE UNIQUE INDEX uq_repositories_identity ON repositories (identity_key);
ALTER TABLE snapshots ADD COLUMN dirty INTEGER NOT NULL DEFAULT 0;
"""

_SCHEMA_V6_EVIDENCE = """
ALTER TABLE evidence ADD COLUMN resolver_id TEXT NOT NULL DEFAULT '';
ALTER TABLE evidence ADD COLUMN resolver_version TEXT NOT NULL DEFAULT '';
ALTER TABLE evidence ADD COLUMN evidence_tier INTEGER NOT NULL DEFAULT 0;
ALTER TABLE evidence ADD COLUMN index_hash TEXT NOT NULL DEFAULT '';
ALTER TABLE evidence ADD COLUMN validation_status TEXT NOT NULL DEFAULT '';
"""

_SCHEMA_V7_FTS_OCC = """
CREATE VIRTUAL TABLE fts_occurrences USING fts5(
    snapshot_id UNINDEXED,
    name,
    terms,
    signature,
    docstring,
    path,
    language UNINDEXED,
    kind UNINDEXED
);
"""


def migrations(*, fts5: bool) -> list[Migration]:
    """Lista de migrações; a v2 (FTS5) só existe quando a build suporta."""
    m = [Migration(1, "core schema (blueprint §6)", _SCHEMA_V1)]
    if fts5:
        m.append(Migration(2, "fts_symbols virtual table", _SCHEMA_V2_FTS))
    m.append(Migration(3, "snapshot-scoped indexes", _SCHEMA_V3_INDEXES))
    m.append(Migration(4, "parse result cache (lf-0020)", _SCHEMA_V4_CACHE))
    m.append(Migration(5, "logical repo identity + dirty flag (lf-0021)", _SCHEMA_V5_IDENTITY))
    m.append(Migration(6, "evidence resolver provenance (lf-0035)", _SCHEMA_V6_EVIDENCE))
    if fts5:
        m.append(Migration(
            7, "fts_occurrences snapshot-scoped lexical index (lf-0036)",
            _SCHEMA_V7_FTS_OCC,
        ))
    return m


def _checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def _split_statements(sql: str) -> Iterator[str]:
    """Divide o script em statements completos via sqlite3.complete_statement.

    Necessário porque ``executescript()`` faz COMMIT implícito antes de
    rodar — sairia da transação do ``with conn:`` e deixaria DDL parcial
    aplicada sem registro em ``schema_migrations`` (Phase 0 / P1.3).
    """
    buf: list[str] = []
    for line in sql.splitlines(keepends=True):
        buf.append(line)
        chunk = "".join(buf)
        if sqlite3.complete_statement(chunk):
            stmt = chunk.strip()
            if stmt:
                yield stmt
            buf.clear()


def migrate(conn: sqlite3.Connection, *, fts5: bool | None = None) -> int:
    """Aplica migrações pendentes; retorna o schema_version resultante."""
    if fts5 is None:
        from code_atlas.storage.db import fts5_available

        fts5 = fts5_available(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        )
        """
    )
    applied = {
        row["version"]: row["checksum"]
        for row in conn.execute("SELECT version, checksum FROM schema_migrations")
    }
    for mig in migrations(fts5=fts5):
        digest = _checksum(mig.sql)
        if mig.version in applied:
            if applied[mig.version] != digest:
                raise MigrationError(
                    f"migration v{mig.version} checksum drift "
                    f"(applied {applied[mig.version][:12]} != current {digest[:12]})"
                )
            continue
        try:
            with conn:
                # sqlite3 legacy só inicia transação implícita em DML —
                # BEGIN explícito garante que DDL também seja atômica.
                conn.execute("BEGIN")
                for stmt in _split_statements(mig.sql):
                    conn.execute(stmt)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, checksum) VALUES (?, ?, ?)",
                    (mig.version, mig.name, digest),
                )
        except sqlite3.Error as exc:
            raise MigrationError(f"migration v{mig.version} failed: {exc}") from exc
    return schema_version(conn)


def schema_version(conn: sqlite3.Connection) -> int:
    """Maior versão aplicada; 0 se nenhuma."""
    try:
        row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
    except sqlite3.Error:
        return 0
    return int(row["v"] or 0)
