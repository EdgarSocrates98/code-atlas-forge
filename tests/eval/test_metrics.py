"""lf-0048 — métricas validadas com valores calculados à mão."""

from __future__ import annotations

import pytest

from code_atlas.eval.metrics import (
    aggregate,
    average_precision_at_k,
    diversity,
    evaluate_query,
    hit_rate_at_k,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)

RANKED = ["a", "b", "c", "d", "e"]
REL = {"a": 2, "c": 1, "x": 1}  # x nunca retornado


def test_precision_denominator_is_k_not_returned() -> None:
    # bug do avaliador v1: era tp/len(returned) → 1/2=0.5 errado
    assert precision_at_k(RANKED[:2], REL, 10) == pytest.approx(0.1)
    assert precision_at_k(RANKED, REL, 3) == pytest.approx(2 / 3)
    assert precision_at_k(RANKED, REL, 0) == 0.0


def test_recall_and_no_relevant_excluded() -> None:
    assert recall_at_k(RANKED, REL, 3) == pytest.approx(2 / 3)
    assert recall_at_k(RANKED, {}, 3) is None
    rec = evaluate_query("q0", RANKED, {}, 3)
    assert "recall_at_k" not in rec.metrics
    assert aggregate([rec]).no_relevant == 1


def test_mrr() -> None:
    assert mrr(RANKED, REL) == 1.0
    assert mrr(["b", "c"], {"c": 1}) == 0.5
    assert mrr(["b"], REL) == 0.0


def test_ndcg_graded_hand_computed() -> None:
    # gains=[2,0,1] → dcg=3 + 0 + 1/log2(4)=3.5
    # ideal=[2,1,1] → idcg=3 + 1/log2(3) + 1/log2(4)=3+0.6309+0.5
    expected = 3.5 / (3 + 1 / 1.584962500721156 + 0.5)
    assert ndcg_at_k(RANKED, REL, 3) == pytest.approx(expected, abs=1e-4)
    # binária = caso grade=1
    assert ndcg_at_k(["a"], {"a": 1}, 1) == 1.0
    assert ndcg_at_k(["b"], {"a": 1}, 1) == 0.0


def test_ap_hand_computed() -> None:
    # hits em rank1 (p=1) e rank3 (p=2/3) → (1+2/3)/min(3,3)=5/9
    assert average_precision_at_k(RANKED, REL, 3) == pytest.approx(5 / 9)


def test_hit_rate_and_diversity() -> None:
    assert hit_rate_at_k(RANKED, REL, 1) == 1.0
    assert hit_rate_at_k(["b"], REL, 1) == 0.0
    assert diversity(["a", "a", "b"]) == pytest.approx(2 / 3)
    assert diversity([]) == 1.0


def test_aggregate_macro_micro_median() -> None:
    r1 = evaluate_query("q1", ["a"], {"a": 1}, 1)
    r2 = evaluate_query("q2", ["x"], {"b": 1}, 1)
    rep = aggregate([r1, r2])
    assert rep.n_queries == 2
    assert rep.macro["precision_at_k"] == pytest.approx(0.5)
    assert rep.median["precision_at_k"] == pytest.approx(0.5)
    # micro: tp=1, returned=2 → 0.5 ; recall: tp=1, rel=2 → 0.5
    assert rep.micro["precision"] == pytest.approx(0.5)
    assert rep.micro["recall"] == pytest.approx(0.5)


def test_misleading_metric_guards() -> None:
    # ranking vazio nunca dá recall alto por divisão estranha
    rec = evaluate_query("q", [], {"a": 1}, 10)
    assert rec.metrics["recall_at_k"] == 0.0
    assert rec.metrics["precision_at_k"] == 0.0
    # duplicatas nunca inflam tp — dedup por identidade na avaliação
    rec2 = evaluate_query("q", ["a", "a", "a"], {"a": 1}, 3)
    assert rec2.tp == 1
    assert rec2.returned == 1
