# ADR-006: SQLite WAL + FTS5

**Status:** decidida e implementada (Cycle 1)

## Contexto

Precisa-se de persistência local embeddable, transacional, com busca
full-text futura — sem servidor.

## Decisão

SQLite como único banco: `journal_mode=WAL`, `foreign_keys=ON`,
`synchronous=NORMAL`, busy timeout 5s, escritor único por
`BEGIN IMMEDIATE`, readers concorrentes, migrações versionadas com
checksum SHA-256 em `schema_migrations`, FTS5 criado quando a build
suporta (flag `fts5_available`, graceful quando não).

## Consequências

- WAL permite `status` (leitor) durante `index` (escritor) — testado com
  threads concorrentes.
- `pip-audit` não requer serviço; DB é arquivo único em data_dir.
- FTS populada só no Cycle 4; a tabela já existe no schema v2.
