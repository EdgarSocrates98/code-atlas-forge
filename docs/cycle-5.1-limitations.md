# Cycle 5.1 — Limitações (declaradas)

- `consumer_modified`/`dependency_removed` refinam ainda pouco: o
  estado diferencia presença/alteração, mas não prova que o consumidor
  AINDA chame o símbolo removido (exigiria diff de arestas por par).
- `content_verified` só prova identidade para revisão=HEAD limpo;
  revisões históricas sem `commit_sha` → `partial` (sem checkout por
  contrato).
- Merge-base de branches divergentes: detectado como mismatch/partial;
  resolução de merge-base dedicada: NOT RUN.
- Corpus grande/real-world: NOT RUN — sem extrapolação de fixtures.
