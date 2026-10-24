"""Evaluation metrics — métricas de retrieval matematicamente
validadas (lf-0048). Sem dependências; determinístico."""

from code_atlas.eval.metrics import (
    MetricRecord,
    aggregate,
    average_precision_at_k,
    diversity,
    hit_rate_at_k,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)

__all__ = [
    "MetricRecord",
    "aggregate",
    "average_precision_at_k",
    "diversity",
    "hit_rate_at_k",
    "mrr",
    "ndcg_at_k",
    "precision_at_k",
    "recall_at_k",
]
