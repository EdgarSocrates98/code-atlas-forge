"""Cycle 5 — Impact Engine E2E sobre snapshots reais."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "golden"))

from retrieval_eval import build_eval_repo

from code_atlas.impact.contracts import ImpactRequest
from code_atlas.impact.engine import ImpactEngine
from code_atlas.indexing import IndexCoordinator


@pytest.fixture()
def two_snapshots(tmp_path):
    root = tmp_path / "repo"
    conn = build_eval_repo(root)
    # segunda revisão: muda corpo + remove função + muda assinatura
    inv = root / "pkg" / "inventory.py"
    inv.write_text(
        "def reserve_stock(sku, qty, warehouse=None):\n"
        "    return True\n")
    (root / "pkg" / "orders.py").write_text(
        "class OrderService:\n"
        "    def cancel_order(self, order_id):\n"
        "        return True\n")
    IndexCoordinator(conn).index(root)
    yield conn, root
    conn.close()


def test_symbol_changes_detected(two_snapshots) -> None:
    conn, root = two_snapshots
    res = ImpactEngine(conn).analyze(
        ImpactRequest(repository_root=str(root)))
    kinds = {s.qualified_name: s.change.value
             for s in res.symbol_changes}
    assert kinds.get("pkg.inventory.release_stock") == "removed"
    assert kinds.get("pkg.orders.OrderService.create_order") == "removed"
    sig = [s for s in res.symbol_changes
           if s.change.value == "signature_changed"]
    assert any("reserve_stock" in s.qualified_name for s in sig)


def test_contract_classification(two_snapshots) -> None:
    conn, root = two_snapshots
    res = ImpactEngine(conn).analyze(
        ImpactRequest(repository_root=str(root)))
    classes = {c.classification for c in res.contract_changes}
    assert "public-symbol-removed" in classes
    # reserve_stock ganhou parâmetro opcional → provavelmente compatível
    opt = [c for c in res.contract_changes
           if "reserve_stock" in c.symbol_key]
    assert opt and opt[0].compatibility == "likely-compatible"


def test_blast_radius_and_risk(two_snapshots) -> None:
    conn, root = two_snapshots
    res = ImpactEngine(conn).analyze(
        ImpactRequest(repository_root=str(root)))
    assert res.risk.category.value in (
        "low", "moderate", "high", "critical")
    for i in res.impacts:
        assert i.path and i.path.relation_ids
        assert isinstance(i.path.fully_verified, bool)


def test_report_renders(two_snapshots) -> None:
    conn, root = two_snapshots
    from code_atlas.impact.report import to_json, to_markdown
    res = ImpactEngine(conn).analyze(
        ImpactRequest(repository_root=str(root)))
    j = to_json(res)
    assert '"schema_version": 1' in j
    md = to_markdown(res)
    assert "# Impact Report" in md and "Risk" in md


def test_no_snapshot_empty_result(tmp_path) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from code_atlas.storage.migrations import migrate
    migrate(conn)
    res = ImpactEngine(conn).analyze(
        ImpactRequest(repository_root=str(tmp_path)))
    assert res.symbol_changes == ()
    assert any(d.code == "snapshot-unavailable"
               for d in res.diagnostics)
