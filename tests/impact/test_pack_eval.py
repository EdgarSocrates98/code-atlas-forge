"""Cycle 5 — Impact packs, eval golden, perf e adversarial."""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "golden"))

from retrieval_eval import build_eval_repo

from code_atlas.impact.contracts import (
    ImpactLimits,
    ImpactRequest,
)
from code_atlas.impact.engine import ImpactEngine
from code_atlas.impact.packs import build_pack
from code_atlas.indexing import IndexCoordinator


@pytest.fixture()
def res(tmp_path):
    root = tmp_path / "repo"
    conn = build_eval_repo(root)
    (root / "pkg" / "orders.py").write_text(
        "class OrderService:\n"
        "    def cancel_order(self, order_id):\n"
        "        return True\n")
    IndexCoordinator(conn).index(root)
    r = ImpactEngine(conn).analyze(
        ImpactRequest(repository_root=str(root)))
    yield conn, r, root
    conn.close()


def test_pack_budget_respected(res) -> None:
    _, r, _root = res
    pack = build_pack(r, 4000)
    assert pack.tokens_used <= 4000
    pack2 = build_pack(r, 60)
    assert pack2.tokens_used <= 60
    if pack2.items and pack.items:
        assert pack2.items[0].priority <= 10  # mudança vem primeiro


def test_pack_critical_omission_declared(res) -> None:
    _, r, _root = res
    pack = build_pack(r, 10)
    if pack.omitted:
        assert any("pack-truncated" in d or "critical-omitted" in d
                   for d in pack.diagnostics)


def test_golden_scenarios_measured(res) -> None:
    """Ground truth do fixture: OrderService.create_order removido."""
    _, r, _root = res
    changes = {s.qualified_name: s.change.value
               for s in r.symbol_changes}
    # precisão/recall de detecção no caso controlado
    expected_removed = {"pkg.orders.OrderService.create_order",
                        "pkg.orders.OrderService"}
    detected_removed = {q for q, c in changes.items()
                        if c == "removed"}
    assert expected_removed & detected_removed  # recall > 0
    assert not r.diagnostics or all(
        d.code != "internal-error" for d in r.diagnostics)


def test_perf_analyze_under_budget(res) -> None:
    conn, _r2, root = res
    eng = ImpactEngine(conn)
    t0 = time.perf_counter()
    for _ in range(5):
        eng.analyze(ImpactRequest(repository_root=str(root)))
    p50 = (time.perf_counter() - t0) / 5
    assert p50 < 2.0, f"analyze p50={p50:.2f}s > 2s"


def test_limits_enforced(res) -> None:
    conn, _r2, root = res
    r = ImpactEngine(conn).analyze(ImpactRequest(
        repository_root=str(root),
        limits=ImpactLimits(max_paths=1, max_changes=2)))
    assert len(r.impacts) <= 1
    assert len(r.symbol_changes) <= 2
    assert r.truncated


def test_invalid_revision_diagnostic(tmp_path) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from code_atlas.storage.migrations import migrate
    migrate(conn)
    # sem git no tmp_path → invalid-revision ou git-error, nunca crash
    r = ImpactEngine(conn).analyze(ImpactRequest(
        repository_root=str(tmp_path), base_revision="zzz-bogus"))
    assert r.diagnostics  # diagnóstico explícito
    conn.close()
