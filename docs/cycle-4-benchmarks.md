# Benchmarks — Cycle 4

Fonte: `benchmarks/results/lf-0045-perf-cycle4.json` (n=30/célula,
mediana+percentis medidos — script `tests/golden/perf_cycle4.py`).

| cenário | p50 ms | p95 ms | p99 ms |
|---|---|---|---|
| small · lexical | 0.18 | 0.20 | 0.20 |
| small · structural | 0.15 | 0.16 | 0.16 |
| small · graph | 0.34 | 0.49 | 0.49 |
| small · hybrid | 1.52 | 1.59 | 1.59 |
| small · pack-focus | 0.30 | 0.34 | 0.34 |
| small · pack-deep | 0.51 | 0.52 | 0.52 |
| small · mcp-search E2E | 1.13 | 1.22 | 1.22 |
| medium · lexical | 0.08 | 0.09 | 0.09 |
| medium · structural | 0.24 | 0.26 | 0.26 |
| medium · graph | 0.12 | 0.15 | 0.15 |
| medium · hybrid | 6.99 | 7.79 | 7.79 |
| medium · pack-deep | 0.23 | 0.25 | 0.25 |
| medium · mcp-search | 0.97 | 1.02 | 1.02 |

Hybrid = soma dos backends (esperado/medido). Index incremental medido
em `benchmarks/results/lf-0031-sqlite-perf.json` (−17% no 5k, Cycle 3.1).
