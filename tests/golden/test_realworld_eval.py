"""lf-0055 — smoke do harness real-world vendored."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def test_realworld_eval_runs(tmp_path) -> None:
    from realworld_eval import run
    out = run(tmp_path)
    assert out["tasks"] == 8
    macro = out["macro"]
    assert set(macro) == {"structural", "lexical", "hybrid"}
    # corpus vendored deve recuperar algo em toda estratégia
    for strat, m in macro.items():
        assert m["recall_at_k"] > 0.0, strat
        assert m["hit_rate_at_k"] > 0.0, strat
    # hybrid não pode ser pior que structural em recall neste corpus
    assert (macro["hybrid"]["recall_at_k"]
            >= macro["structural"]["recall_at_k"])


def test_realworld_judgments_valid() -> None:
    tasks = json.loads(
        Path(__file__).with_name(
            "realworld_judgments.json").read_text())["tasks"]
    assert len(tasks) == 8
    cats = {t["category"] for t in tasks}
    assert len(cats) == 8  # uma por categoria da spec
    langs = {t["language"] for t in tasks}
    assert langs == {"python", "typescript"}
