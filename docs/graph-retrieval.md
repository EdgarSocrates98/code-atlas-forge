# Graph Retrieval (lf-0037)

BFS limitado sobre `relations` persistidas (snapshot-scoped):
seeds por qname/último segmento → `TraversalLimits{max_depth,
max_results≤200}` → vizinhos batchados (sem N+1). Truncamento gera
diagnóstico tipado. `--strategy graph`. **Honesto**: arestas
syntactic-unresolved não entram — recall de grafo no eval é baixo por
decisão, não por bug (ver cycle-4-limitations).
