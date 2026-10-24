# Retrieval Evaluation v2

`src/code_atlas/eval/metrics.py` — correções sobre o avaliador v1:

- **Precision@K = tp/K** (era tp/len(returned) — inflava quando retornado<K).
- Recall@K, MRR, NDCG binário **e graduado**, MAP@K, HitRate@K, FPR, diversity.
- Micro e macro agregação + mediana; por categoria e linguagem.
- **Dedup por identidade canônica** — hits duplicados não inflam tp.
- `tests/eval/test_metrics.py`: valores calculados à mão; fixture
  detecta métrica enganosa (P=1/K, não 1/n).

Dataset: `tests/golden/retrieval_judgments_v2.json` — 8 tarefas,
relevância graduada 0..2 + guarda sem-relevantes.
