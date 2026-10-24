# Database Schema — Code Atlas (schema_version 5)

Fonte de verdade: `src/code_atlas/storage/migrations.py` (v1 core,
v2 FTS, v3 índices snapshot-scoped, v4 cache de ParseResult, v5
identidade lógica + dirty).
Este documento espelha o que as migrações criam — qualquer drift aqui
é bug.

## schema_migrations

| coluna | tipo | nota |
|---|---|---|
| version | INTEGER PK | sequencial |
| name | TEXT | descritivo |
| checksum | TEXT | sha256 do SQL aplicado (anti-drift) |
| applied_at | TEXT | utc |

## repositories

`id PK`, `origin`, `root_handle` (path resolvido — **diagnóstico desde
v5**, atualizado a cada index), `git_provider`, `default_branch`,
`created_at`, **`identity_key` (v5, UNIQUE)**.

`identity_key` (lf-0021): identidade lógica — `git-remote:<url>` →
`git-root:<sha raiz>` → `path:<root>`. Realocar o diretório físico
mantém o repo; dois checkouts do mesmo remote compartilham o repo.
Rows pré-v5 recebem `path:<root_handle>` na migração.

## snapshots (máquina de estados BUILDING→READY | FAILED, READY→STALE)

`id PK`, `repository_id FK→repositories`, `git_ref`, `commit_sha NULL`,
**`dirty` INTEGER (v5, default 0)** — worktree diverge do `commit_sha`,
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

## symbols (**populada no Cycle 2** — upsert por identidade lógica)

`id PK`, `repository_id FK`, `language`, `qualified_name`, `kind`,
`UNIQUE(repository_id, language, qualified_name, kind)`.

Kinds persistidos: file, module, class, interface, function, method,
symbol (nós `repository`/`package` do grafo são deriváveis de path e
não persistem).

## symbol_occurrences (**populada no Cycle 2**)

`snapshot_id FK CASCADE`, `logical_symbol_id FK→symbols`,
`source_file_id FK`, `span_start/span_end/line/col`, `content_hash`,
`parse_status` ∈ `parsed` | `parsed-with-errors` | `parse-failed`.

## relations (**populada no Cycle 2** — arestas entre ocorrências)

`snapshot_id FK CASCADE`, `from_id`, `to_id NULL`, `to_unresolved NULL`,
`type CHECK(9 edges Blueprint)`, `resolution_status CHECK`,
`confidence_class CHECK`, `resolver NOT NULL`, `evidence_ids` (JSON array).

`from_id`/`to_id` referenciam `symbol_occurrences.id` — endpoints sempre
dentro do mesmo snapshot (invariante verificado em `foreign_key_check`
+ validação cross-snapshot antes do READY).

## evidence (**populada no Cycle 2**)

`snapshot_id FK CASCADE`, `source_file_id FK`, `path`, `span`,
`content_hash NOT NULL`, `extractor NOT NULL`, `extractor_version NOT NULL`, `notes`.

## index_jobs

`repository_id FK`, `snapshot_id FK NULL`,
`status CHECK('running','success','failed','noop')`, `started_at`,
`finished_at`, `counts` JSON, `failures` JSON, `manifest_hash`.

## fts_symbols (v2, só se FTS5 disponível)

`CREATE VIRTUAL TABLE fts_symbols USING fts5(qualified_name, language, kind, content='')`
— populada desde o Cycle 2 (insert por símbolo novo); a busca atual usa
`LIKE` parametrizado — FTS é caminho de upgrade, não dependência.

## Índices

v1: `idx_snapshots_repo_status(repository_id,status)`,
`idx_file_inventory_snapshot(snapshot_id)`,
`idx_source_files_repo_path(repository_id,relative_path)`,
`idx_occurrences_snap_file(snapshot_id,source_file_id)`,
`idx_relations_from(from_id,type)`, `idx_relations_to(to_id,type)`.

v3 (snapshot-scoped, Cycle 2): `idx_relations_snapshot(snapshot_id)`,
`idx_occurrences_snapshot_symbol(snapshot_id,logical_symbol_id)`,
`idx_evidence_snapshot(snapshot_id)`.

v4 (lf-0020): `idx_cache_repo_path(repository_id, path)` +
constraint `UNIQUE(repository_id, path, content_sha256, language,
parser_version, grammar_version, extractor_version, resolver_version,
builder_version, config_digest)`.

v5 (lf-0021): `uq_repositories_identity(identity_key)` UNIQUE — chave
da unicidade lógica e do `INSERT OR IGNORE` race-safe.

## parse_result_cache (v4)

Cache versionado de `ParseResult` por arquivo (lf-0020) — **não
pertence a snapshots** (isolamento: cada snapshot materializa linhas
próprias; o cache só acelera a re-obtenção do artefato de parse).

| coluna | tipo | nota |
|---|---|---|
| id | INTEGER PK | |
| repository_id | FK→repositories | |
| path | TEXT | rel POSIX |
| content_sha256 | TEXT | identidade de conteúdo |
| language / parser_version / grammar_version / extractor_version / resolver_version / builder_version / config_digest | TEXT | **chave de validade completa** — qualquer campo novo invalida |
| payload | TEXT | ParseResult serializado em JSON (sem pickle) |
| payload_sha256 | TEXT | integridade na leitura — diverge → miss |
| last_used_snapshot_id | INTEGER NULL | GC: retenção nos últimos N READY |
| hit_count | INTEGER | telemetria |
| created_at | TEXT | utc |

GC (dentro da transação de publicação): remove entradas cujo
`last_used_snapshot_id` é anterior ao N-ésimo READY mais recente
(`KEEP_SNAPSHOTS=5`) e limita a `MAX_ENTRIES=50_000` por repo (eviction
LRU por `last_used_snapshot_id`).

## Pragmas

`journal_mode=WAL`, `foreign_keys=ON`, `synchronous=NORMAL`,
busy timeout via `connect(timeout=5000ms)`. Leitores concorrentes sob WAL;
escritor único via `BEGIN IMMEDIATE`.
