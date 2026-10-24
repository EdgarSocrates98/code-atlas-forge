# ADR-0024 — Arquitetura FTS (lf-0036)

## Decisão
`fts_occurrences`: FTS5 **regular** (não contentless) com
`snapshot_id UNINDEXED` e `rowid = occurrence_id`. Tokenização de
identificadores em Python (split camelCase/snake_case) — sem
extensão C via sqlite3 stdlib.

## Por quê
- Isolamento por snapshot: contentless repo-level (v6) não permitia
  filtrar por snapshot; tabela regular permite `WHERE snapshot_id=?`.
- Deleção normal no GC de snapshots (contentless exigiria
  `delete-all` por índice).
- Signature/docstring vêm dos `GraphNode` no momento do persist —
  nenhuma coluna nova em `symbol_occurrences`.
- Stemming: **não** aplicado (prefix match via `*`); honesto —
  `order` não casa `orders` em vez de fingir semântica.
