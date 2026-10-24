# Hybrid Search (RRF k=60)

`code-atlas search "q" --strategy hybrid` funde lexical+structural+graph
(+vector opt-in) por **rank**, nunca score bruto. `score = Σ 1/(60+rank)`,
`ranking_version=rrf-k60-v1`. Dedup por `qualified_name` — hits do mesmo
símbolo fundem preservando `matched_strategies`, `per-strategy rank` e
evidências. Explain: cada hit traz `rrf=rank:score` por estratégia.
Métricas: `eval-report.json → cycle4.strategies.hybrid`.
