# Arquitetura — Cycle 3.1 (Semantic Hardening)

> Status: **entregue** — branch `cycle-3.1-semantic-hardening` → main.
> Diferença para o Cycle 3: os resolvers externos deixam de ser código
> isolado e passam a compor o pipeline de indexação.

## Pipeline de indexação (caminho real)

```
scan ──▶ diff/detector ──▶ planner ──▶ cache.get_many ──▶ parse
  │                                                        │
  ▼                                                        ▼
manifest ──────────────▶ graph.build ──▶ enrich ──▶ persist ──▶ validate ──▶ publish (+GC)
```

1. **Discovery**: `scan()` produz `ScanResult` com `hash_ms` (timing
   agregado de `_hash_file` — metadado de execução, fora do manifest).
2. **Change detection + planner** (`lf-0019/21`): REUSE / REVALIDATE /
   reparse por arquivo; `resolver:` e `resolver:scip=<sha16>` entram na
   identidade do índice (`_analyzer_versions`) — índice SCIP novo
   invalida.
3. **Cache de parse** (`lf-0019/20`, lf-0031): `ParseCache.get_many`
   lê em lote (chunks de 400 via `json_each`, SQL estático); blob
   corrompido → miss honesto.
4. **Graph build**: `_resolve_relations` resolve módulo de contexto em
   O(1) via `_by_path`/`_nid_mod` (lf-0031 — era scan linear O(n²)).
5. **Semantic enrichment** (`lf-0027`): `SemanticEnricher` re-resolve
   arestas do baseline sintático contra resolvers externos —
   `GraphEdge.source_path` dá o módulo de origem. Promoção só ocorre
   quando o alvo é persistível; arestas mantêm `resolver`,
   `resolution_status`, `confidence_class` por aresta.
6. **Bootstrap de resolvers** (`lf-0027/28`): `[resolvers]` do
   `config.toml` do **operador** (nunca do repo indexado). SCIP passa
   por `assess_revision` (lf-0028): `rejected` nunca resolve,
   `degraded` capa confidence em `heuristic`. LSP: opt-in, lifecycle
   endurecido (lf-0029).
7. **Persistência + publicação** (transação única): FK check antes do
   publish; snapshots anteriores → STALE; GC opt-in
   `[indexing].retain_snapshots` (lf-0031) deleta STALE excedentes na
   mesma transação (READY/FAILED preservados).

## Instrumentação de estágio (lf-0031)

`IndexCoordinator` acumula `stage_ms_*` via `time.monotonic()` (barato,
sem tracemalloc — lição do Cycle 3): `discovery`, `hashing`,
`invalidation`, `cache_lookup`, `parse`, `graph_build`, `semantic`,
`validation`, `persist_rows`, `fk_check`, `publish_gc`,
`persist_publish`. Expõe-se em `index --json` → `stats`.

## Contratos e identidades (inalterados no ciclo)

- Envelope v2 (`schema_version: "2"`), campos aditivos apenas.
- `relations.resolver`: `syntactic-scope-v1` (baseline), parser-id
  (`tree-sitter-*` em arestas estruturais), `scip`, `lsp:<server>`.
- Identidade do índice: parsers + `resolver:` + `resolver:scip=<sha16>`.
- Snapshot: READY único por repo; consultas só leem READY.

## O que mudou vs Cycle 3

| Ciclo 3 | Cycle 3.1 |
|---|---|
| resolvers SCIP/LSP existiam mas isolados | integrados via `SemanticEnricher` + bootstrap |
| `resolver` = parser-id em arestas | baseline normalizado `syntactic-scope-v1` |
| índice SCIP aceito sem revisão | `IndexProvenance` + `assess_revision` (7 sinais) |
| LSP mínimo | `documentSymbol`, `publishDiagnostics`, recovery 1×restart+1×retry |
| sem modelo de grafo incremental | ADR-0020: snapshots completos (sharing rejeitado) |
| sem instrumentação de pipeline | `stage_ms_*` por estágio |
| cache 1 query/arquivo | `get_many` batch + builder O(1) |
| sem status semântico | `semantic-status`, `--scip`, `--resolver` |
