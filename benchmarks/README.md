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
