# ADR-0019 — Cache de ParseResult (schema v4)

## Status

Aceito (lf-0020, Cycle 3).

## Contexto

Reuso incremental precisava de um artefato estável entre snapshots.
Copiar linhas persistidas quebraria o isolamento lógico (toda linha é
snapshot-scoped). Re-parsear tudo anularia o incremental.

## Decisão

Tabela `parse_cache` (schema v4): `ParseResult` serializado em **JSON
canônico** (nunca pickle — inspecionável, sem risco de desserialização
arbitrária). `CacheKey` inclui conteúdo + idioma + todas as versões que
influenciam o output + `config_digest` da configuração efetiva.

GC: `last_used_snapshot_id` tracking, `KEEP_SNAPSHOTS=5`,
`MAX_ENTRIES=50_000`, tudo dentro da transação do snapshot.

## Consequências

- Cache miss → parse real (nunca inventa dados).
- Payload auditável como JSON — diff entre entradas é legível.
- Versão nova de qualquer componente invalida sem migração.
