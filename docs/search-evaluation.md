# Search Evaluation (lf-0044)

Harness: `tests/golden/retrieval_eval.py` + `retrieval_judgments.json`
(8 tarefas, ground truth commitado). Métricas: P@K, R@K, MRR, NDCG@K,
evidence-validity, FP, latência (n=3, mediana+range). Bloco
`cycle4.strategies` no `eval-report.json` — números medidos, gaps
honestos (graph recall baixo por arestas unresolved; lexical/structural
sem `evidence`).
