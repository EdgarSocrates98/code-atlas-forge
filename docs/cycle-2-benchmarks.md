# Benchmarks Cycle 2 — pipeline de indexação

Medido nesta máquina com `benchmarks/bench_index.py` sobre corpus
sintético seeded (`seed=20261024`) — arquivos Python parseáveis com
imports, herança e calls resolvidas/não-resolvidas. Artefato completo:
`benchmarks/results/bench-index-latest.json`.

Ambiente: `Linux-7.0.0-38-generic-x86_64-with-glibc2.39`,
Python `3.12.3`.

## Resultados

| arquivos | total | discovery | parse | graph | persist* | no-op | query p50 | query p95 | RSS pico | DB+WAL | símbolos | relações |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 100 | 0.57s | 0.016s | 0.086s | 0.009s | 0.46s | 0.018s | 0.34ms | 0.38ms | 344MB | 0.62MB | 520 | 810 |
| 1000 | 5.10s | 0.127s | 0.867s | 0.144s | 3.96s | 0.123s | 2.6ms | 3.0ms | 344MB | 3.5MB | 5 020 | 8 010 |
| 5000 | 26.5s | 0.61s | 4.29s | 1.24s | 20.4s | 0.56s | 12.5ms | 13.4ms | 344MB | 33.7MB | 25 020 | 40 010 |

\* `persist_residual_s` = total − (discovery + parse + graph) medidos
fora do coordinator — inclui o segundo scan interno do coordinator e o
trabalho transacional; é um residual honesto, não instrumentação interna.

## Leitura

- **Parse**: ~0.86ms/arquivo Python — linear e previsível.
- **Persist** domina o total (~77% em 5000): inserts por linha + FK
  checks + WAL. Candidato óbvio de otimização (batch maior, índices
  diferidos) em ciclo futuro — **registrado como débito P2**.
- **No-op reindex**: 0.12s em 1000 arquivos — hashing+scan dominam;
  nenhum parse reexecuta (garantido por teste).
- **Query**: p95 ≈ 13ms com 25k símbolos — LIKE parametrizado é
  suficiente nesta escala; FTS5 permanece caminho de upgrade.
- **Memória**: ~344MB pico em 5000 arquivos (tracemalloc + ru_maxrss).

## Comparação honesta com Cycle 1

`bench_scan.py` (Cycle 1) mediu **somente discovery+hash**: ~9.3k
arq/s (500) → ~13.7k arq/s (5000). Comparável apenas com `discovery_s`
aqui (0.61s/5000 ≈ 8.2k arq/s — mais lento porque o corpus agora é
`.py` real, não bytes aleatórios). Não existe "total pipeline" de
Cycle 1 para comparar — o pipeline não existia.

## Reproduzir

```bash
.venv/bin/python benchmarks/bench_index.py --sizes 100 1000 5000 \
    --json benchmarks/results/bench-index-latest.json
```

Determinístico por seed: mesmo `--sizes`/seed → mesma árvore de corpus.
