# ADR-0025 — Hybrid Ranking RRF (lf-0038)

## Decisão
Reciprocal Rank Fusion, `RRF_K = 60`, ranking_version `rrf-k60-v1`.
Fusão sobre **ranks**, nunca scores brutos de backends heterogêneos.
Dedup por `symbol_key` (qualified_name canônico); evidências e ranks
de todas as estratégias preservadas por hit.

## Por quê
- Scores bm25 / estruturais / vectoriais são incomensuráveis sem
  normalização frágil; RRF é o baseline da literatura (k=60 padrão).
- Dedup por símbolo (não occurrence) exigido pela spec: def e uso do
  mesmo símbolo fundem numa entrada com evidências somadas.
- Vector entra como quarto ranker somente quando habilitado —
  ausência não altera o pipeline.
