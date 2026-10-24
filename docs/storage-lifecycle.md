# Ciclo de vida do storage — GC e VACUUM (lf-0031)

Decisão explícita sobre crescimento do banco, avaliada na Phase 7 do
Cycle 3.1. Modelo de grafo: snapshots completos por publicação
(ADR-0020) — cada `index` bem-sucedido escreve ~110k linhas num corpus
de 5k arquivos.

## O que cresce

| tabela | por snapshot (5k) | cascade no delete do snapshot |
|---|---|---|
| `file_inventory` | ~5k linhas | sim |
| `symbol_occurrences` | ~25k | sim |
| `relations` | ~45k | sim |
| `evidence` | ~35k | sim |
| `snapshots` | 1 | — |
| `index_jobs` | 1 | FK sem cascade → `snapshot_id = NULL` |
| `parse_result_cache` | 0 (reuso) | GC próprio por retenção/`last_used` |

Snapshots STALE acumulam todas as linhas acima para sempre — esse é o
vetor de crescimento real medido (`db_size_mb` cresce ~linear com o
número de indexações bem-sucedidas).

## Decisão: GC opt-in, não automático

`[indexing] retain_snapshots = N` (`config.toml`):

- **default `0` = ilimitado.** O histórico de snapshots é a trilha de
  auditoria do produto (quem indexou o quê, quando). Apagar por default
  destruiria evidência silenciosamente — mesma razão pela qual `FAILED`
  nunca é podado.
- **`N > 0`** = mantém os `N` STALE mais recentes; o resto é deletado
  **na mesma transação de publicação** (atomicidade com o READY novo —
  nunca existe janela sem snapshot utilizável).
- READY nunca é tocado; `parent_snapshot_id` dos sobreviventes é
  anulado antes do delete (FK sem cascade); `index_jobs.snapshot_id`
  idem — o job permanece com counts/failures intactos.

Implementação: `Store.gc_stale_snapshots` (`store.py`), reportado em
`stats["snapshots_pruned"]` do job.

## VACUUM — política documentada, não automática

`DELETE` marca páginas livres mas não encolhe o arquivo. Opções
avaliadas:

1. **`PRAGMA auto_vacuum = INCREMENTAL` no connect — rejeitado.**
   Trocar o modo exige `VACUUM` full no banco existente (reescreve o
   arquivo inteiro, segura lock exclusivo) e adiciona freelist-tracking
   por página em todo insert/update — custo contínuo para benefício
   marginal num banco cujo padrão é append-por-snapshot.
2. **`VACUUM` automático pós-GC — rejeitado.** Reescreve o DB inteiro
   dentro do que deveria ser uma indexação rápida; em 5k o VACUUM pode
   custar mais que a indexação medida.
3. **Política adotada: manual e offline.** Após habilitar retenção e
   podar muitos snapshots, rodar uma vez:

   ```bash
   sqlite3 ~/.local/share/code-atlas/atlas.db "PRAGMA wal_checkpoint(TRUNCATE); VACUUM;"
   ```

   WAL precisa de checkpoint antes do VACUUM para o WAL não ficar
   carregando páginas mortas.

## Instrumentação (stage_ms_*)

O job report (`index` payload `stats`) agora separa por etapa —
`time.monotonic` puro, sem `tracemalloc` no caminho medido:

`discovery`, `hashing` (acumulado em `_hash_file` dentro do scanner),
`invalidation` (plano incremental), `cache_lookup` (fetch batchado),
`parse`, `graph_build`, `semantic`, `validation` (invariantes do
grafo), `persist_rows` (inserts), `fk_check` (`foreign_key_check`),
`publish_gc` (publish + cache gc + snapshot gc) e o agregado
`persist_publish`.

## O que foi medido e NÃO otimizado (honestidade)

- `result_from_payload` (desserialização do cache) ≈ 1s no 5k — C-side
  do `json.loads` + construção de dataclasses; sem caminho stdlib mais
  rápido que preserve o codec auditável.
- `persist` ≈ 1.4s — O(snapshot) é o preço decidido pelo ADR-0020;
  zero duplicatas encontradas em `relations`/`occurrences`
  (`dup_groups = 0` medido); `executemany` já em uso; nenhum índice
  removível identificado (os 3 de `relations` servem queries reais).
- `_hash_file` re-hasheia todos os arquivos a cada scan (~0.5s no 5k) —
  é a prova de integridade do incremental; pular via mtime/size foi
  rejeitado (mtime é reescrito por operações git — lição lf-0028).
- PRAGMAs: `synchronous=NORMAL`/`journal_mode=WAL`/`cache_size=-64000`
  mantidos — nenhum enfraquecimento de durabilidade foi justificável
  pelos números (o custo não está em fsync, está em row work).
