"""Métricas de retrieval — definições canônicas, sem atalhos.

Convenções (auditadas no baseline cycle-4.1):
- ``precision_at_k`` = |relevantes ∩ top-K| / K  (denominador K, não
  len(returned) — o bug do avaliador v1).
- ``recall_at_k`` = |relevantes ∩ top-K| / |relevantes|; None quando a
  consulta não tem relevantes (excluída da agregação, contada em
  ``no_relevant``).
- ``ndcg_at_k`` aceita relevância graduada (``dict[id -> grade]``);
  binária é o caso grade=1.
- Agregação: macro-mean (por consulta) + micro (somas globais) +
  mediana — nunca uma só estatística.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

Relevant = dict[str, int]  # doc_id -> grau de relevância (>=1)


def _topk(ranked: list[str], k: int) -> list[str]:
    return list(ranked[: max(0, k)])


def precision_at_k(ranked: list[str], relevant: Relevant, k: int) -> float:
    if k <= 0:
        return 0.0
    tp = sum(1 for d in _topk(ranked, k) if relevant.get(d, 0) > 0)
    return tp / k


def recall_at_k(ranked: list[str], relevant: Relevant, k: int) -> float | None:
    rel = {d for d, g in relevant.items() if g > 0}
    if not rel:
        return None  # sem relevantes → excluída da agregação
    tp = sum(1 for d in _topk(ranked, k) if d in rel)
    return tp / len(rel)


def mrr(ranked: list[str], relevant: Relevant) -> float:
    for i, d in enumerate(ranked):
        if relevant.get(d, 0) > 0:
            return 1.0 / (i + 1)
    return 0.0


def _dcg(gains: list[int]) -> float:
    return sum(
        (2.0 ** g - 1.0) / math.log2(i + 2) for i, g in enumerate(gains)
    )


def ndcg_at_k(ranked: list[str], relevant: Relevant, k: int) -> float:
    if not relevant or k <= 0:
        return 0.0
    gains = [relevant.get(d, 0) for d in _topk(ranked, k)]
    ideal = sorted(relevant.values(), reverse=True)[:k]
    idcg = _dcg(ideal)
    return _dcg(gains) / idcg if idcg else 0.0


def average_precision_at_k(
    ranked: list[str], relevant: Relevant, k: int,
) -> float:
    """AP@K — média das precision@i nos ranks relevantes."""
    rel = {d for d, g in relevant.items() if g > 0}
    if not rel:
        return 0.0
    hits = 0
    acc = 0.0
    for i, d in enumerate(_topk(ranked, k)):
        if d in rel:
            hits += 1
            acc += hits / (i + 1)
    return acc / min(len(rel), k)


def hit_rate_at_k(ranked: list[str], relevant: Relevant, k: int) -> float:
    return 1.0 if any(
        relevant.get(d, 0) > 0 for d in _topk(ranked, k)) else 0.0


def diversity(ranked: list[str]) -> float:
    """Fração de identidades distintas — 1.0 = sem duplicatas."""
    return len(set(ranked)) / len(ranked) if ranked else 1.0


@dataclass
class MetricRecord:
    """Uma consulta avaliada — micro-agregação deriva dos contadores."""

    query_id: str
    k: int
    tp: int = 0
    returned: int = 0
    relevant: int = 0
    metrics: dict[str, float] = field(default_factory=dict)


@dataclass
class EvalReport:
    macro: dict[str, float]
    micro: dict[str, float]
    median: dict[str, float]
    n_queries: int
    no_relevant: int

    def to_dict(self) -> dict[str, object]:
        return {
            "macro": self.macro, "micro": self.micro,
            "median": self.median,
            "n_queries": self.n_queries,
            "no_relevant": self.no_relevant,
        }


def aggregate(records: list[MetricRecord]) -> EvalReport:
    keys = sorted({k for r in records for k in r.metrics})
    macro: dict[str, float] = {}
    median: dict[str, float] = {}
    for key in keys:
        vals = [r.metrics[key] for r in records if key in r.metrics]
        if vals:
            macro[key] = statistics.fmean(vals)
            median[key] = statistics.median(vals)
    n_ret = sum(r.returned for r in records)
    n_rel = sum(r.relevant for r in records)
    n_tp = sum(r.tp for r in records)
    micro = {
        "precision": n_tp / n_ret if n_ret else 0.0,
        "recall": n_tp / n_rel if n_rel else 0.0,
    }
    return EvalReport(
        macro=macro, micro=micro, median=median,
        n_queries=len(records),
        no_relevant=sum(1 for r in records if r.relevant == 0),
    )


def evaluate_query(
    query_id: str,
    ranked: list[str],
    relevant: Relevant,
    k: int,
) -> MetricRecord:
    """Calcula todas as métricas de uma consulta.

    Dedup por identidade preservando ordem — hits duplicados não
    inflam tp/precision (audit: unidade de avaliação = identidade
    canônica, não ocorrência)."""
    seen: set[str] = set()
    div = diversity(ranked)
    dedup: list[str] = []
    for d in ranked:
        if d not in seen:
            seen.add(d)
            dedup.append(d)
    ranked = dedup
    top = _topk(ranked, k)
    tp = sum(1 for d in top if relevant.get(d, 0) > 0)
    rel = sum(1 for g in relevant.values() if g > 0)
    metrics: dict[str, float] = {
        "precision_at_k": precision_at_k(ranked, relevant, k),
        "mrr": mrr(ranked, relevant),
        "ndcg_at_k": ndcg_at_k(ranked, relevant, k),
        "ap_at_k": average_precision_at_k(ranked, relevant, k),
        "hit_rate_at_k": hit_rate_at_k(ranked, relevant, k),
        "diversity": div,
    }
    rec = recall_at_k(ranked, relevant, k)
    if rec is not None:
        metrics["recall_at_k"] = rec
    return MetricRecord(
        query_id=query_id, k=k, tp=tp, returned=len(top),
        relevant=rel, metrics=metrics)
