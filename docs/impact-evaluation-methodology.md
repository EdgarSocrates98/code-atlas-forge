# Impact Evaluation Methodology

- Fixtures `tests/golden` + corpus vendored; métricas P/R medidas no
  harness `tests/impact` (selector precision assert ≥0.9 medido).
- Métricas declaradas só quando medidas: cross-repo isolation (test
  A/B mesmo banco), binding states (5/5 cobertos), removed-symbol
  recall (reconciliação), multi-origin (visited por origem).
- Não extrapolar fixtures pequenas para projetos grandes — corpus
  público grande: **NOT RUN** neste ciclo.
