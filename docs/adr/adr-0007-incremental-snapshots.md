# ADR-007: Indexação incremental com snapshots

**Status:** decidida e implementada (Cycle 1 — nível inventário)

## Contexto

Indexar é caro; reindexar sem mudanças não pode refazer trabalho nem
corromper o estado anterior.

## Decisão

Snapshots versionados com máquina `BUILDING → READY | FAILED`, `READY → STALE`.
Identidade lógica (`source_files` por repo+path) separada de ocorrência
(`file_inventory` por snapshot). O digest do manifest canônico
(`manifest_sha256` — sem timestamps) decide no-op: digest+analyzer_versions
iguais ao READY atual → job `noop`, zero escrita estrutural. Publicação é
transação única; falha → rollback + FAILED separado, READY anterior intocado.
`--full` força novo snapshot mesmo com digest idêntico.

## Consequências

- Dois scans idênticos produzem manifest bytes-idênticos (testado).
- Crash mid-transaction nunca deixa meio-snapshot visível.
- Cycle 3 herdará a máquina de estados para invalidação por arquivo.
