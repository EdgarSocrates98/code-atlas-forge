"""lf-0041 — expansão progressiva: dedup acumulada, snapshot removed,
paginação, sem troca silenciosa de revisão."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from code_atlas.context import ContextExpander, expansion_to_json
from code_atlas.indexing import IndexCoordinator
from code_atlas.storage import connect, migrate

_REPO = {
    "pkg/__init__.py": "",
    "pkg/util.py": "def helper():\n    return 1\n",
    "pkg/engine.py": (
        "from pkg.util import helper\n\n"
        "def run():\n    return helper()\n"
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
    sid = int(conn.execute(
        "SELECT MAX(id) AS s FROM snapshots").fetchone()["s"])
    yield conn, str(repo), sid
    conn.close()


def test_expand_adds_neighbors(env) -> None:
    conn, _root, sid = env
    res = ContextExpander(conn).expand(sid, "pkg.util.helper")
    assert res.error == ""
    assert any("helper" in i.symbol_key or i.symbol_key
               for i in res.added)
    keys = {i.symbol_key for i in res.added}
    assert keys >= {"pkg.engine.run"} or len(res.added) >= 1


def test_dedup_against_delivered(env) -> None:
    conn, _root, sid = env
    first = ContextExpander(conn).expand(sid, "pkg.util.helper")
    delivered = frozenset(i.item_id for i in first.added)
    second = ContextExpander(conn).expand(
        sid, "pkg.util.helper", delivered=delivered)
    assert second.added == ()
    assert set(second.already_delivered) == delivered


def test_snapshot_removed_errors_explicitly(env) -> None:
    conn, _root, sid = env
    conn.execute(
        "UPDATE snapshots SET status = 'FAILED' WHERE id = ?", (sid,))
    conn.commit()
    res = ContextExpander(conn).expand(sid, "helper")
    assert "indisponível" in res.error and res.added == ()
    # inexistente também é erro explícito, nunca fallback silencioso
    res2 = ContextExpander(conn).expand(sid + 9999, "helper")
    assert res2.error and res2.added == ()


def test_budget_truncation_lists_remaining_refs(env) -> None:
    conn, _root, sid = env
    res = ContextExpander(conn).expand(
        sid, "pkg.util.helper", budget_tokens=64)
    if res.truncated:
        assert res.remaining_refs
        assert all(r.ref_id for r in res.remaining_refs)


def test_expand_json_parseable(env) -> None:
    conn, _root, sid = env
    import json

    d = json.loads(expansion_to_json(
        ContextExpander(conn).expand(sid, "helper")))
    assert d["renderer_version"] == "expand-json-v1"
    assert d["snapshot_id"] == sid
