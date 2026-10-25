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

_SCHEMA_V8_VECTORS = """
CREATE TABLE vector_embeddings (
    snapshot_id   INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    occurrence_id INTEGER NOT NULL REFERENCES symbol_occurrences(id) ON DELETE CASCADE,
    symbol_key    TEXT NOT NULL,
    model_id      TEXT NOT NULL,
    model_version TEXT NOT NULL,
    dim           INTEGER NOT NULL,
    content_hash  TEXT NOT NULL,
    vector        BLOB NOT NULL,
    PRIMARY KEY (snapshot_id, occurrence_id, model_id)
);
CREATE INDEX idx_vector_embeddings_snap ON vector_embeddings(snapshot_id, model_id);
CREATE TABLE embedding_cache (
    model_id      TEXT NOT NULL,
    model_version TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    dim           INTEGER NOT NULL,
    vector        BLOB NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (model_id, model_version, content_hash)
);
"""


_SCHEMA_V9_DOMAINS = """
-- Federação de domínios (lf-0110 / ADR-0010): tabelas dedicadas porque
-- (a) relations.type tem CHECK que não aceita o vocabulário de domínio e
-- SQLite não altera CHECK sem rebuild; (b) a identidade de entidades de
-- domínio inclui catalog/namespace/environment, inexpressável no UNIQUE
-- de symbols (repo, language, qualified_name, kind). Aditiva: bancos
-- antigos abrem e ganham as três tabelas vazias.

CREATE TABLE domain_entities (
    id INTEGER PRIMARY KEY,
    repository_id INTEGER NOT NULL REFERENCES repositories(id),
    domain TEXT NOT NULL,
    kind TEXT NOT NULL,
    identity_key TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    catalog TEXT NOT NULL DEFAULT '',
    namespace TEXT NOT NULL DEFAULT '',
    environment TEXT NOT NULL DEFAULT '',
    attributes TEXT NOT NULL DEFAULT '{}',
    UNIQUE (repository_id, identity_key)
);

CREATE TABLE domain_entity_occurrences (
    id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES domain_entities(id),
    source_file_id INTEGER REFERENCES source_files(id),
    span_start INTEGER,
    span_end INTEGER,
    line INTEGER,
    col INTEGER,
    content_hash TEXT,
    attributes TEXT NOT NULL DEFAULT '{}',
    extraction_status TEXT NOT NULL DEFAULT 'extracted'
);

CREATE TABLE domain_relations (
    id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    from_entity_occ_id INTEGER REFERENCES domain_entity_occurrences(id),
    from_symbol_occ_id INTEGER REFERENCES symbol_occurrences(id),
    to_entity_occ_id INTEGER REFERENCES domain_entity_occurrences(id),
    to_symbol_occ_id INTEGER REFERENCES symbol_occurrences(id),
    to_unresolved TEXT,
    type TEXT NOT NULL CHECK (type IN (
        'CONTAINS','DEFINES','IMPORTS','CALLS','IMPLEMENTS',
        'EXTENDS','REFERENCES','CONFIGURES','TESTS',
        'READS_FROM','WRITES_TO','TRANSFORMS','DERIVES_FROM',
        'DEPENDS_ON','CALLS_ENDPOINT','IMPLEMENTS_CONTRACT',
        'PROVISIONS','USES_RESOURCE','ASSUMES_ROLE','TRIGGERS','DEPLOYS')),
    resolution_status TEXT NOT NULL CHECK (resolution_status IN (
        'resolved','ambiguous','unresolved','external')),
    confidence_class TEXT NOT NULL CHECK (confidence_class IN (
        'resolved','syntactic','heuristic','inferred')),
    resolver TEXT NOT NULL,
    evidence_ids TEXT NOT NULL DEFAULT '[]',
    CHECK (from_entity_occ_id IS NOT NULL OR from_symbol_occ_id IS NOT NULL),
    CHECK (to_entity_occ_id IS NOT NULL OR to_symbol_occ_id IS NOT NULL
           OR to_unresolved IS NOT NULL)
);

CREATE INDEX idx_domain_entities_repo
    ON domain_entities (repository_id, domain, kind);
CREATE INDEX idx_domain_occ_snapshot
    ON domain_entity_occurrences (snapshot_id, entity_id);
CREATE INDEX idx_domain_occ_entity
    ON domain_entity_occurrences (entity_id);
CREATE INDEX idx_domain_relations_from
    ON domain_relations (from_entity_occ_id, type);
CREATE INDEX idx_domain_relations_to
    ON domain_relations (to_entity_occ_id, type);
CREATE INDEX idx_domain_relations_snap
    ON domain_relations (snapshot_id, type);
"""


_SCHEMA_V10_FEDERATION = """
-- Workspaces federados (lf-0118 / Cycle 7 Phase 10): agrupa >=2
-- repositórios já indexados numa "view" multi-repo. Estritamente
-- aditiva — bancos antigos abrem e ganham as duas tabelas vazias.
--
-- ``federation_members.pinned_snapshot_id`` é NULLABLE e carrega
-- ``ON DELETE SET NULL``: quando o GC de retenção remove um snapshot
-- STALE que estava pinado, o pin é limpo e o membro volta a seguir o
-- READY mais recente — nunca FK dangling nem ponteiro para dados
-- removidos. ``repository_id`` não tem cascade: repositórios não são
-- removidos por nenhum caminho hoje (só snapshots viram GC).

CREATE TABLE federation_workspaces (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE federation_members (
    id INTEGER PRIMARY KEY,
    workspace_id INTEGER NOT NULL
        REFERENCES federation_workspaces(id) ON DELETE CASCADE,
    repository_id INTEGER NOT NULL REFERENCES repositories(id),
    pinned_snapshot_id INTEGER REFERENCES snapshots(id) ON DELETE SET NULL,
    added_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (workspace_id, repository_id)
);

CREATE INDEX idx_federation_members_ws
    ON federation_members (workspace_id);
CREATE INDEX idx_federation_members_repo
    ON federation_members (repository_id);
"""


_SCHEMA_V11_DOMAIN_REL_INDEXES = """
-- lf-0132 (Cycle 8 Phase 6): índices compostos nos quatro endpoints de
-- ``domain_relations``. O ego (``from_/to_entity_occ_id IN (...)``) e o
-- BFS de linhagem filtram por endpoint dentro do snapshot; os índices
-- single-column de v9 lideram pela coluna de endpoint e não cobrem o
-- predicado ``snapshot_id = ?``, então o planner preferia o range-scan
-- de ``idx_domain_relations_snap`` (~O(snapshot)) mesmo para INs
-- pequenos. ``(snapshot_id, endpoint)`` dá probes diretos nos INs de um
-- termo e habilita MULTI-INDEX OR nas egos **quando há estatísticas**.
--
-- ``ANALYZE`` escopo-domínio popula ``sqlite_stat1`` em bancos já
-- povoados — sem stats o planner mantém o scan de snapshot até para os
-- INs literais do ego e os probes PK de ``deo.id IN (json_each)`` na
-- hidratação de endpoints. ANALYZE só escreve a tabela de sistema
-- ``sqlite_stat1``: nenhuma tabela de aplicação muda de forma. O
-- refresh contínuo fica em ``Store.publish_snapshot`` (mesma tx da
-- publicação — stats commitam com os dados que descrevem).
--
-- Estritamente aditiva: bancos antigos abrem e ganham os quatro
-- índices + stats; nada é dropado (os índices single-column de v9
-- permanecem — ``(endpoint, type)`` cobre formas diferentes).

CREATE INDEX IF NOT EXISTS idx_domain_relations_snap_from_ent
    ON domain_relations (snapshot_id, from_entity_occ_id);
CREATE INDEX IF NOT EXISTS idx_domain_relations_snap_to_ent
    ON domain_relations (snapshot_id, to_entity_occ_id);
CREATE INDEX IF NOT EXISTS idx_domain_relations_snap_from_sym
    ON domain_relations (snapshot_id, from_symbol_occ_id);
CREATE INDEX IF NOT EXISTS idx_domain_relations_snap_to_sym
    ON domain_relations (snapshot_id, to_symbol_occ_id);

ANALYZE domain_relations;
ANALYZE domain_entity_occurrences;
ANALYZE domain_entities;
"""


_SCHEMA_V12_DOMAIN_FTS = """
-- lf-0133 (Cycle 8 Phase 7): FTS5 dedicado a entidades de domínio.
--
-- Standalone, mesmo desenho de ``fts_occurrences`` (v7): rowid = id da
-- ``domain_entity_occurrences`` — join direto para âncora snapshot/
-- entidade. ``terms`` carrega identificadores pré-tokenizados
-- (``search/tokenizer.index_terms``) — snake_case/camelCase/dotted
-- recuperáveis por fragmento, enquanto ``name``/``qualified_name``
-- guardam a forma exata. ``snapshot_id``/``entity_id``/``domain``/
-- ``kind`` são UNINDEXED: filtros de escopo, nunca alvos de MATCH.
--
-- ``content=`` externo não serve: as colunas de match moram em
-- ``domain_entities``/``source_files``, e cascades de FK não disparam
-- triggers em SQLite — a limpeza é manual em
-- ``Store.gc_stale_snapshots`` (mesmo mecanismo de fts_occurrences).
--
-- Migração separada da v11 por duas razões: (a) FTS5 é opcional de
-- build — um gate na v11 também removeria os índices dos builds sem
-- módulo; (b) o checksum por-versão exige SQL determinístico — variantes
-- condicionais na mesma versão gerariam drift entre builds. Bancos sem
-- FTS5 ficam na v11 com índices e LIKE bounded intactos.
--
-- Populada em ``persist_domain_result``: somente snapshots **novos**
-- ganham rows — snapshots pré-v12 seguem no LIKE honesto do
-- ``DomainEntitySearch`` (fallback declarado). Backfill in-place por
-- snapshot existe em ``Store.rebuild_domain_fts``; um ``code-atlas
-- reindex`` de superfície é o equivalente operacional (nova snapshot
-- já sai coberta pelo persist).

CREATE VIRTUAL TABLE domain_fts USING fts5(
    snapshot_id UNINDEXED,
    entity_id UNINDEXED,
    name,
    qualified_name,
    terms,
    path,
    domain UNINDEXED,
    kind UNINDEXED
);
"""


def migrations(*, fts5: bool) -> list[Migration]:
    """Lista de migrações; v2/v7/v12 (FTS5) só existem quando a build suporta."""
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
    m.append(Migration(
        8, "vector embeddings + content cache (lf-0039)",
        _SCHEMA_V8_VECTORS,
    ))
    m.append(Migration(
        9, "domain entities + relations (lf-0110)",
        _SCHEMA_V9_DOMAINS,
    ))
    m.append(Migration(
        10, "federation workspaces + members (lf-0118)",
        _SCHEMA_V10_FEDERATION,
    ))
    m.append(Migration(
        11, "domain_relations endpoint indexes + stats (lf-0132)",
        _SCHEMA_V11_DOMAIN_REL_INDEXES,
    ))
    if fts5:
        m.append(Migration(
            12, "domain_fts dedicated FTS5 (lf-0133)",
            _SCHEMA_V12_DOMAIN_FTS,
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
    # lf-0155 fail-safe: ``schema_migrations`` com versão fora do
    # catálogo completo = banco de build mais nova — migrar seria
    # silencioso e inseguro; recusa honesta, nunca segue adiante.
    # O catálogo fts5=True é o superset (v2/v7/v12 existem sempre) —
    # um banco v12 num build sem FTS5 é caminho projetado (fallback
    # LIKE), não versão desconhecida.
    catalog = migrations(fts5=True)
    by_version = {m.version: m for m in catalog}
    unknown = sorted(v for v in applied if v not in by_version)
    if unknown:
        raise MigrationError(
            f"schema_migrations has unknown versions {unknown} — "
            "database was written by a newer build; upgrade code-atlas")
    applicable = {m.version for m in migrations(fts5=fts5)}
    for mig in catalog:
        digest = _checksum(mig.sql)
        if mig.version in applied:
            if applied[mig.version] != digest:
                raise MigrationError(
                    f"migration v{mig.version} checksum drift "
                    f"(applied {applied[mig.version][:12]} != current {digest[:12]})"
                )
            continue
        if mig.version not in applicable:
            continue  # migração FTS5-gated — não aplicável nesta build
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
