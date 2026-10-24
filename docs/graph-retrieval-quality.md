# Graph Retrieval Quality (Phase 2)

Causa raiz do recall≈0 (baseline): seeds só por qname exato/substring
+ calls `x.method()` unresolved (sem `to_id` navegável).

Fix:

- **Multi-seed**: qname exato + top lexical + top structural hits.
- **Intent determinístico** (`search/intent.py`): consumers→incoming,
  dependencies→outgoing, flow/architecture→both, tests→incoming,
  bug/debug→both.
- Seeds rotulados `graph-seed`; evidência carrega `dir=`+relation+ids.

Medido no fixture v2 (macro): R 0.40→**1.0**, NDCG 0.17→**0.65**.
Nenhuma aresta inventada — traversal só segue `relations` persistidas.
