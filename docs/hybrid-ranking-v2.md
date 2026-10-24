# Hybrid Ranking v2 (Phase 3)

- Chave canônica composta `lang:kind:qname` (dedup por símbolo real,
  não por ocorrência).
- RRF k=60 preservado como baseline (`enable_rerank=False`).
- Rerank task-aware **opt-in**: boost de match exato de qname, boost
  de evidência de direção (`dir=`), demote sem evidência. Fatores
  documentados no código — explicável, sem LLM.
- Benchmark v1×v2 no fixture: **delta 0** — honestamente registrado;
  o mecanismo dispara (teste unitário), o dataset é pequeno demais
  para separar os rankings.
