# Database Schema — Code Atlas (schema_version 2)

Fonte de verdade: `src/code_atlas/storage/migrations.py` (v1 core, v2 FTS).
Este documento espelha o que as migrações criam — qualquer drift aqui é bug.

## schema_migrations

| coluna | tipo | nota |
|---|---|---|
| version | INTEGER PK | sequencial |
| name | TEXT | descritivo |
| checksum | TEXT | sha256 do SQL aplicado (anti-drift) |
| applied_at | TEXT | utc |

## repositories

`id PK`, `origin`, `root_handle` (path resolvido — **limitação C1: mover a
pasta cria novo repo**), `git_provider`, `default_branch`, `created_at`.

## snapshots (máquina de estados BUILDING→READY | FAILED, READY→STALE)

`id PK`, `repository_id FK→repositories`, `git_ref`, `commit_sha NULL`,
`worktree_digest` (sha256 do manifest canônico), `analyzer_versions` (JSON),
`parent_snapshot_id FK→snapshots NULL`, `status CHECK('BUILDING','READY','FAILED','STALE')`, `error`, `created_at`.

- No máximo **um READY por repo** — `publish_snapshot` vira STALE nos demais
  na mesma transação.
- `FAILED` é inserido em transação separada para diagnóstico pós-rollback.

## source_files (identidade lógica por repo+path)

`id PK`, `repository_id FK`, `relative_path` (POSIX relativo), `language`,
`sha256 NULL`, `size_bytes`, `ignored`, `UNIQUE(repository_id, relative_path)`.

## file_inventory (ocorrência por snapshot)

`snapshot_id FK ON DELETE CASCADE`, `source_file_id FK`, `sha256`,
`size_bytes`, `ignored`, `PRIMARY KEY(snapshot_id, source_file_id)`.

## symbols (schema presente, sem writers no Cycle 1)

`id PK`, `repository_id FK`, `language`, `qualified_name`, `kind`,
`UNIQUE(repository_id, language, qualified_name, kind)`.

## symbol_occurrences (schema presente, sem writers)

`snapshot_id FK CASCADE`, `logical_symbol_id FK→symbols`,
`source_file_id FK`, `span_start/span_end/line/col`, `content_hash`,
`parse_status`.

## relations (schema presente, sem writers)

`snapshot_id FK CASCADE`, `from_id`, `to_id NULL`, `to_unresolved NULL`,
`type CHECK(9 edges Blueprint)`, `resolution_status CHECK`,
`confidence_class CHECK`, `resolver NOT NULL`, `evidence_ids` (JSON array).

## evidence (schema presente, sem writers)

`snapshot_id FK CASCADE`, `source_file_id FK`, `path`, `span`,
`content_hash NOT NULL`, `extractor NOT NULL`, `extractor_version NOT NULL`, `notes`.

## index_jobs

`repository_id FK`, `snapshot_id FK NULL`,
`status CHECK('running','success','failed','noop')`, `started_at`,
`finished_at`, `counts` JSON, `failures` JSON, `manifest_hash`.

## fts_symbols (v2, só se FTS5 disponível)

`CREATE VIRTUAL TABLE fts_symbols USING fts5(qualified_name, language, kind, content='')`
— sem rows no Cycle 1 (FTS populada no Cycle 4).

## Índices

`idx_snapshots_repo_status(repository_id,status)`,
`idx_file_inventory_snapshot(snapshot_id)`,
`idx_source_files_repo_path(repository_id,relative_path)`,
`idx_occurrences_snap_file(snapshot_id,source_file_id)`,
`idx_relations_from(from_id,type)`, `idx_relations_to(to_id,type)`.

## Pragmas

`journal_mode=WAL`, `foreign_keys=ON`, `synchronous=NORMAL`,
busy timeout via `connect(timeout=5000ms)`. Leitores concorrentes sob WAL;
escritor único via `BEGIN IMMEDIATE`.
