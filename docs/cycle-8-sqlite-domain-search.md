# Cycle 8 — SQLite de domínio: índices + FTS dedicado (lf-0132, lf-0133)

Phase 6 (performance audit) e Phase 7 (full-text search de domínio).
Números medidos em `benchmarks/results/sqlite-latest.json`, regenerados
por `benchmarks/bench_sqlite.py` — nunca metas. Fixture mid-size
determinística (seed 20261208, 800 artefatos .sql): ~12.5k entidades,
~12.5k ocorrências, ~12k arestas de domínio.

## Schema

| versão | conteúdo | condição |
|---|---|---|
| v11 | 4 índices `(snapshot_id, endpoint)` em `domain_relations` + `ANALYZE` das 3 tabelas de domínio | sempre (aditiva) |
| v12 | `domain_fts` — FTS5 standalone, rowid = `domain_entity_occurrences.id` | só builds com FTS5 |

v12 é separada da v11 porque FTS5 é opcional de build: bancos sem o
módulo param na v11 com índices e LIKE bounded intactos. O checksum
por-versão exige SQL determinístico — variante condicional na mesma
versão geraria drift entre builds.

`domain_fts` é standalone (não `content=` externo) porque os campos de
match moram em três tabelas (`domain_entities`, `domain_entity_occurrences`,
`source_files`) e FK cascades não alcançam vtables — a limpeza é manual
em `Store.gc_stale_snapshots`, mesmo desenho de `fts_occurrences`.

Colunas: `snapshot_id`, `entity_id`, `domain`, `kind` são UNINDEXED
(filtros de escopo); `name`/`qualified_name` guardam a forma exata;
`terms` carrega `search.tokenizer.index_terms` (fragmentos snake/camel/
dotted); `path` é o `relative_path` da ocorrência ("" quando não ancorada).

## Política de população — decisão registrada

- **Snapshots novos**: `persist_domain_result` grava uma row FTS por
  ocorrência, na mesma transação do snapshot (rowid = id da ocorrência).
- **Snapshots pré-v12**: **não são retroalimentados** — a faceta cai no
  LIKE bounded de sempre com diagnóstico `domain-fts-fallback`
  (declarado, nunca silencioso).
- **Backfill**: `Store.rebuild_domain_fts(snapshot_id)` — delete+insert
  escopo-snapshot, idempotente, retorna rows regravadas. Equivale ao
  `code-atlas reindex` operacional (reindex já sai coberto pelo persist).
- Banco migrado por build com FTS5 aberto por build sem: `Store`
  chequeia tabela **e** capacidade (`fts5_available`) antes de escrever.

## Semântica de match — diferenças honestas

LIKE é substring ASCII case-insensitive; FTS5 `unicode61` casa por
token com case-fold **e remoção de diacríticos** (`sao` casa `São` só
no FTS — testado nos dois caminhos, nunca assumido). `terms` cobre
fragmentos: `mart.orders` casa `mart.v_orders` por token — declarado
como `matched_fields=("terms",)` quando o teste de substring não
reproduz o match. Prefixo `foo*` só em `terms`. Fallbacks: query não
tokenizável → LIKE; `OperationalError` de MATCH → LIKE +
`domain-fts-error`; tabela ausente (pré-v12) → LIKE sem diagnóstico.

BM25 (pesos name 10 > qname 8 > terms 6 > path 1) ordena os candidatos
**dentro** do teto de scan — exato primeiro; a ordem pública final
segue o contrato de sempre (score > identidade). `bm25()` não entra em
agregado: subquery com `LIMIT -1` projeta o rank e `MIN(f.bm)` agrega.

`via_relation` virou UNION de dois probes indexados (o OR cruzava dois
aliases e impedia multi-index OR): 4.4–6.8 ms → **0.016 ms** medido.

## Planner e estatísticas

`publish_snapshot` roda `ANALYZE` escopo-domínio na transação de
publicação (stats commitam com os dados; `PRAGMA optimize` foi medido e
não re-analisa tabelas crescidas de forma confiável). Migração v11
analisa bancos já povoados.

Achados EXPLAIN (planos completos embutidos no JSON de resultados):

- ego `from_/to_entity_occ_id IN (…)`: scan de snapshot → **MULTI-INDEX
  OR** nos índices v11 (2.79→0.08 ms; tail 3.15→0.04 ms);
- hidratação `deo.id IN json_each`: range-scan de snapshot → **probes
  por rowid** (1.0→0.03 ms);
- `entity_detail` summaries: scan → probes `(snapshot_id, endpoint)`
  por elemento do IN (1.99→0.03 ms);
- leituras full-snapshot (crossresolve, federação): `SCAN` em ordem de
  rowid — sem temp b-tree de ORDER BY, estritamente melhor.

Residuais (medidos, documentados — ver `residual_known_limits` no
JSON): passo de BFS de linhagem (OR de 4 `json_each`, ~2.2 ms — rewrite
exigiria `handlers.py`); FTS em termo ultra-comum custa bm25 por row e
pode empatar com o scan LIKE — o valor do FTS é seletividade +
semântica de token, não velocidade universal.

PRAGMAs (inalterados, reportados): `journal_mode=wal`,
`synchronous=NORMAL`, `foreign_keys=ON`, `busy_timeout=5000`,
`cache_size=-64000`.
