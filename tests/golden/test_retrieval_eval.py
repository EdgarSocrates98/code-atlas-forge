"""lf-0044 — eval de retrieval/contexto é reproduzível e honesto."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retrieval_eval import JUDGMENTS, retrieval_block


@pytest.fixture(scope="module")
def block():
    return retrieval_block(Path(tempfile.mkdtemp()), runs=3)


def test_judgments_verifiable() -> None:
    j = json.loads(JUDGMENTS.read_text())
    assert len(j["tasks"]) == 8
    for t in j["tasks"]:
        assert t["relevant"] and t["required_context"], t["id"]


def test_metrics_in_range(block) -> None:
    for strat, m in block["strategies"].items():
        for k in ("precision", "recall", "mrr", "ndcg",
                  "evidence_validity"):
            assert 0.0 <= m[k] <= 1.0, (strat, k)
        lo, hi = m["latency_range_ms"]
        assert 0 <= lo <= hi


def test_deterministic(block) -> None:
    b2 = retrieval_block(Path(tempfile.mkdtemp()), runs=1)
    for strat in block["strategies"]:
        assert (
            block["strategies"][strat]["recall"]
            == b2["strategies"][strat]["recall"]
        )


def test_context_budget_and_not_run(block) -> None:
    ctx = block["context"]
    assert ctx["budget_violations"] == 0
    assert 0.0 <= ctx["missing_critical_rate"] <= 1.0
    assert block["not_run"]  # provider ausente declarado
