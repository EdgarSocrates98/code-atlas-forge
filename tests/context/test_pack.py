"""lf-0040 — context pack engine: níveis, budget, dedup, renderers."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from code_atlas.context import (
    ContextEngine,
    ContextRequest,
    render_json,
    render_markdown,
)
from code_atlas.indexing import IndexCoordinator
from code_atlas.storage import connect, migrate

_REPO = {
    "pkg/__init__.py": "",
    "pkg/util.py": (
        'def helper():\n    """Utility helper function."""\n    return 1\n'
    ),
    "pkg/engine.py": (
        "from pkg.util import helper\n\n"
        "def run():\n    return helper()\n"
    ),
    "pkg/cli.py": (
        "from pkg.engine import run\n\n"
        "def main():\n    return run()\n"
    ),
}


@pytest.fixture()
def env(tmp_path: Path):
    repo = tmp_path / "repo"
    for rel, c in _REPO.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(c)
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    IndexCoordinator(conn).index(repo)
    conn.row_factory = sqlite3.Row
    yield conn, str(repo)
    conn.close()


def _pack(env, task="helper", level="focus", budget=4000):
    conn, root = env
    return ContextEngine(conn).build(ContextRequest(
        task=task, repository_root=root, level=level,
        budget_tokens=budget))


def test_pack_envelope_and_estimates_declared(env) -> None:
    p = _pack(env)
    assert p.manifest.schema_version == 1
    assert p.manifest.tokens_estimated is True
    assert p.manifest.estimator.startswith("heuristic-chars")
    assert p.manifest.snapshot_id > 0
    assert any("helper" in s for s in p.selected_symbols)


def test_levels_orientation_focus_deep(env) -> None:
    o = _pack(env, level="orientation")
    f = _pack(env, level="focus")
    d = _pack(env, level="deep")
    assert len(d.items) >= len(o.items)
    assert len(f.items) >= 1
    assert o.manifest.level == "orientation"


def test_budget_respected_and_truncation_honest(env) -> None:
    for budget in (512, 2000, 4000, 8000, 16000):
        p = _pack(env, budget=budget)
        spent = sum(i.est_tokens for i in p.items)
        assert spent <= p.budget.available_items, (budget, spent)
        if p.manifest.truncated:
            assert "pack-truncated" in p.diagnostics.codes
            assert all(r.ref_id for r in p.expansion_refs)


def test_insufficient_budget_flags(env) -> None:
    p = _pack(env, budget=64)
    if p.diagnostics.insufficient_budget:
        assert p.diagnostics.mandatory_context_omitted
        assert p.diagnostics.suggested_minimum_budget > 0
        assert "insufficient-budget" in p.diagnostics.codes


def test_dedup_and_stable_ids(env) -> None:
    p1 = _pack(env)
    p2 = _pack(env)
    ids1 = [i.item_id for i in p1.items]
    assert ids1 == [i.item_id for i in p2.items]
    assert len(ids1) == len(set(ids1))


def test_renderers_versioned(env) -> None:
    p = _pack(env)
    j = json.loads(render_json(p))
    assert j["renderer_version"] == "pack-json-v1"
    assert j["manifest"]["snapshot_id"] == p.manifest.snapshot_id
    md = render_markdown(p)
    assert "pack-markdown-v1" in md and "## " in md
    assert render_json(p) == render_json(_pack(env))


def test_dependencies_provenance(env) -> None:
    p = _pack(env, task="run", level="deep", budget=16000)
    dep_items = [i for i in p.items if i.kind == "dependency"]
    for d in dep_items:
        assert all(e.kind == "relation" for e in d.evidence)
