# Consistência de Snapshots

## Máquina de estados

`BUILDING → READY` (sucesso) ou `→ FAILED` (erro). Snapshot READY
anterior vira `STALE` quando um novo READY publica.

## Publicação atômica

Toda a construção do snapshot — source_files, inventário, símbolos,
ocorrências, evidências, relações, FTS, cache puts — acontece dentro
de **uma** transação `BEGIN IMMEDIATE` (escritor único por workspace).

Antes do `publish_snapshot`:
- `PRAGMA foreign_key_check` — integridade referencial real.
- Validação de contagens/estado (`IndexCoordinator._validate`).

Falha → `rollback` total: nada parcial vira READY. Falha honesta
deixa rastro inspecionável: job `failed` + snapshot `FAILED`.

## Re-check dentro do lock

Após adquirir `BEGIN IMMEDIATE`, o coordinator re-lê o último READY —
dois indexadores concorrentes não produzem `success`+`success` para
entrada idêntica (o segundo vira no-op). Testado com 60 execuções
concorrentes sem falha (lf-0015 + lf-0020).

## No-op honesto

Digest do manifesto + versões de analyzer idênticos → no-op antes de
qualquer re-parse. `force=True`/`--full` ignora o atalho.

## Dirty flag

`snapshots.dirty` (schema v5) registra quando o conteúdo indexado
diverge do `commit_sha` — consumidores podem distinguir snapshot de
HEAD limpo de snapshot com trabalho não-commitado.
