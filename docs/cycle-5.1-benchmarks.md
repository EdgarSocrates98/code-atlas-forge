# Cycle 5.1 — Benchmarks

Medições nesta máquina (Linux, Python 3.12, fixtures pequenas):

| estágio | medida |
|---|---|
| analyze() completo (eval fixture) | p50 ~50ms (suite test_pack_eval) |
| blast_radius 3 origens | <5ms |
| resolver+binding | <2ms (+ scan só quando content_verify exigido) |
| test_selector base+target | <2ms |

Corpus grande: **NOT RUN** — números acima são fixtures pequenas, sem
extrapolação. Metodologia: pytest timing, n=1 por teste, máquina local.
