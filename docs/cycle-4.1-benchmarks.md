# Cycle 4.1 — Benchmarks

`benchmarks/results/lf-0056-perf-cycle41.json` — fixture EVAL_REPO, n=20/célula:

| estágio | p50 ms | p95 ms |
|---|---|---|
| hybrid RRF (candidatos) | 2.096 | 2.371 |
| hybrid RRF+rerank | 2.070 | 2.221 |
| evidence occurrence_ref | 0.007 | 0.009 |
| context build + enforce serializado | 0.018 | 0.045 |
| render_json | 0.175 | 0.185 |
| token estimate do pack | 0.177 | 0.186 |
| MCP atlas_evidence (in-process) | 0.985 | 1.290 |

Rerank ≈ neutro (2.07 vs 2.10 ms) — custo desprezível. Grounding é
sub-milissegundo. Context enforcement serializa 1× extra por build.
