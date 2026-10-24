# Benchmarks — Cycle 3

## Correção de metodologia (lf-0023)

O baseline Cycle-2 (~26.5s total, ~20.4s "residual de persist" no 5k)
media `index()` sob `tracemalloc` — overhead 3-4× absorvido pelo
residual. Tracemalloc removido; `ru_maxrss` reporta memória sem custo.
**Números Cycle-2 × Cycle-3 não são comparáveis.**

## Perfil medido (5k, corpus v2, Python 3.12.3, Linux)

cProfile no coordinator real: `_parse_files` 3.17s (~42%) >
`builder.build` 1.47s > `persist_graph` 1.42s > `scanner.scan` 1.09s.

## Otimização medida

Batched persist (`ensure_symbols`, `INSERT…RETURNING` chunks=400,
`ensure_source_files`, FTS batch) + `PRAGMA cache_size=-64000`:

- statements por index: **~145k → ~5k (−96%)**
- wall-clock cold index 5k: 5.53-5.59s → ~5.32s (~4%)

Conclusão honesta: o custo dominante de persist é trabalho in-row do
SQLite (insert + manutenção de índice/FK) — irredutível sem enfraquecer
integridade. Parse é o dominante real do cold index.

## Cold index (pós, corpus v2)

| arquivos | total | discovery | parse | graph | residual | noop | query p50/p95 | RSS | DB |
|---|---|---|---|---|---|---|---|---|---|
| 100 | 0.11s | 0.02 | 0.03 | 0.01 | 0.05 | 0.018s | 0.4/0.4ms | 32MB | 1.2MB |
| 1000 | 1.02s | 0.11 | 0.28 | 0.15 | 0.49 | 0.118s | 2.6/3.1ms | 65MB | 17MB |
| 5000 | 5.54s | 0.53 | 1.62 | 1.34 | 2.05 | 0.558s | 12.9/17.1ms | 213MB | 81MB |

## Matriz de cenários (incremental × full, mesmo estado do corpus)

`matches_full` = nós+arestas idênticos ao `index(force=True)` — **true
em todos os cenários × tamanhos (100/1000/5000)**:

| cenário @5000 | incremental | full | parsed | reused |
|---|---|---|---|---|
| noop | 0.54s | — | 0 | — |
| single_file_change | 5.06s | 6.38s | 1 | 5019 |
| imported_module_change | 5.38s | 6.48s | 470 | 4550 |
| rename | 5.64s | 6.22s | 1 | 5019 |
| delete | 5.50s | 6.60s | 0 | 5019 |
| parser_version_change | 6.87s | 6.87s | 5020 | 0 |
| resolver_version_change | 6.77s | 6.65s | 5020 | 0 |
| full_rebuild | 6.57s | — | 5020 | 0 |

**Incremental é limitado por persist+graph O(snapshot), não por
parse** — economia real ~15-20% no 5k. Dedup de linhas entre snapshots
é trabalho futuro (decisão de produto, quebraria isolamento).

Artefatos: `benchmarks/results/index-79cbab8-post.json`,
`benchmarks/results/scenarios-79cbab8-post.json`. Reproduzir:

```bash
.venv/bin/python benchmarks/bench_index.py --sizes 100 1000 5000
.venv/bin/python benchmarks/bench_scenarios.py --sizes 100 1000 5000
```
