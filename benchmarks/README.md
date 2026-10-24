# Benchmarks do Code Atlas

`bench_scan.py` gera um corpus sintético **determinístico** (seed fixo
`20261024`, extensões/tamanhos/profundidades sorteadas com `random.Random`)
e mede o scan completo (walk + fingerprints SHA-256) e um re-scan "warm"
(cache de FS do SO). O no-op por digest é medido no coordinator e não aqui.

## Uso

```bash
.venv/bin/python benchmarks/bench_scan.py --files 500 --json
.venv/bin/python benchmarks/bench_scan.py --files 5000 --repeats 5 --json
.venv/bin/python benchmarks/bench_scan.py --files 10000 --keep  # inspeciona corpus
```

## Saída de referência (Cycle 1)

Medida em Linux 6.x, Python 3.12.3, disco local, dev machine
(execute localmente para comparar — números são de referência, não SLA):

```json
{"files_requested": 500,  "files_found": 500,
 "cold_scan_s": {"median": 0.054}, "warm_scan_s": {"median": 0.051},
 "files_per_s_cold_median": 9324}

{"files_requested": 5000, "files_found": 5000,
 "cold_scan_s": {"median": 0.365}, "warm_scan_s": {"median": 0.350},
 "files_per_s_cold_median": 13702}
```

Escalabilidade aproximadamente linear em ~10k arquivos/s para SHA-256
completo neste hardware — serve como tripwire de regressão para Cycle 2+
(não otimizar para além da corretude).

## Regras

- Números só entram aqui se produzidos pelo script commitado — nunca
  transcritos à mão.
- Não enfraquecer limites do scanner para melhorar o benchmark.
- Corpus gerado em `tempfile` e removido ao final (manter com `--keep`).

---

# Indexação (lf-0013 + lf-0023)

`bench_index.py` mede o pipeline completo (total + fases isoladas +
residual de persist) e `bench_scenarios.py` cobre a matriz de 9 cenários
incrementais com comparação canônica incremental × full.

```bash
.venv/bin/python benchmarks/bench_index.py --sizes 100 1000 5000 --json out.json
.venv/bin/python benchmarks/bench_scenarios.py --sizes 100 1000 5000 --json out.json
```

## Nota de metodologia (lf-0023)

O baseline do Cycle 2 reportava ~26.5s total / ~20.4s "residual de
persist" no corpus de 5k. **Esse número era artefato de instrumentação**:
o bench rodava o `index()` sob `tracemalloc`, que infla workloads
intensivos em alocação 3-4×. Como o residual era `total_traced − fases
não-traced`, ele absorvia o overhead inteiro. No lf-0023 o tracemalloc
foi removido (`ru_maxrss` reporta memória sem custo) — números do
Cycle 2 não são comparáveis aos do Cycle 3.

## Perfil medido (5k, corpus v2, Python 3.12.3, Linux)

cProfile no caminho real do coordinator (~40% de overhead de tracing):

| fase | cumtime | nota |
|---|---|---|
| `_parse_files` | 3.17s | dominante (~42%) — tree-sitter + verificação de integridade |
| `builder.build` | 1.47s | universo de escopo + resolução |
| `persist_graph` | 1.42s | ~110k linhas: símbolos/ocorrências/evidências/relações |
| `scanner.scan` | 1.09s | walk + hash verificado ×2 por arquivo |

## Otimização aplicada (medida)

Perfil mostrava **145k chamadas `sqlite3.execute` por index** —
o candidato óbvio era overhead de round-trip. Após o batching
(`ensure_symbols`, `INSERT … RETURNING` em chunks de 400,
`ensure_source_files`, FTS batch): statements caíram para ~5k (−96%),
mas o tempo ficou ~estável — prova de que o custo dominante de persist
é o trabalho **in-row** do SQLite (inserção + manutenção de índice/FK),
irredutível sem enfraquecer integridade. Ganhos reais:

| mudança | efeito medido (5k, corpus v1, não-profilado) |
|---|---|
| batching de símbolos/ocorrências/evidências/FTS | 5.53-5.59s → ~5.39s; statements −96% |
| `PRAGMA cache_size = -64000` (64MiB) | ~5.32s (−4% vs baseline) |

## Resultados (pós-otimização, corpus v2)

`benchmarks/results/index-79cbab8-post.json` — cold index:

| arquivos | total | discovery | parse | graph | residual | noop | query p50/p95 | RSS | DB |
|---|---|---|---|---|---|---|---|---|---|
| 100 | 0.11s | 0.02 | 0.03 | 0.01 | 0.05 | 0.018s | 0.4/0.4ms | 32MB | 1.2MB |
| 1000 | 1.02s | 0.11 | 0.28 | 0.15 | 0.49 | 0.118s | 2.6/3.1ms | 65MB | 17MB |
| 5000 | 5.54s | 0.53 | 1.62 | 1.34 | 2.05 | 0.558s | 12.9/17.1ms | 213MB | 81MB |

`benchmarks/results/scenarios-79cbab8-post.json` — matriz de cenários
(`matches_full` = nós+arestas idênticos ao `index(force=True)` do mesmo
estado do corpus):

| cenário @5000 | incremental | full do mesmo estado | parsed | reused | match |
|---|---|---|---|---|---|
| noop | 0.54s | — | 0 | — | — |
| single_file_change | 5.06s | 6.38s | 1 | 5019 | ✓ |
| imported_module_change | 5.38s | 6.48s | 470 | 4550 | ✓ |
| rename | 5.64s | 6.22s | 1 | 5019 | ✓ |
| delete | 5.50s | 6.60s | 0 | 5019 | ✓ |
| parser_version_change | 6.87s | 6.87s | 5020 | 0 | ✓ |
| resolver_version_change | 6.77s | 6.65s | 5020 | 0 | ✓ |
| full_rebuild | 6.57s | — | 5020 | 0 | ✓ |

## Conclusões honestas

- **Parse domina o cold index** (~42% cumtime). Otimizações futuras
  relevantes vivem em tree-sitter/diagnósticos, não em SQLite.
- **Incremental é limitado por persist+graph, não por parse**: mesmo com
  5019/5020 arquivos servidos do cache, o snapshot novo re-escreve ~110k
  linhas (isolamento por snapshot). Economia real no 5k: ~15-20%.
  Deduplicação de linhas entre snapshots é trabalho futuro (quebraria o
  modelo de isolamento — decisão de produto, não engenharia).
- `resolver:` versions agora entram na identidade do índice (o detector
  já tratava a chave; nada a produzia) — upgrade do SyntacticResolver
  invalida relações de todos os arquivos corretamente.
- Nenhum check de integridade/segurança foi removido ou enfraquecido.
