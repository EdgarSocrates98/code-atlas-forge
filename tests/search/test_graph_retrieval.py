"""lf-0037 — graph-aware retrieval: travessia limitada, direção, ciclos,
explicabilidade e ranking estrutural."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from code_atlas.indexing import IndexCoordinator
from code_atlas.search import SearchEngine, SearchRequest
from code_atlas.search.graph import GraphRetrieval, TraversalLimits
from code_atlas.storage import connect, migrate

# grafo: engine.run chama helper + greeter; cli chama run; cycle: a↔b
_REPO = {
    "pkg/__init__.py": "",
    "pkg/util.py": (
        "def helper():\n    return 1\n\n"
        "def greeter():\n    return helper()\n"
    ),
    "pkg/engine.py": (
        "from pkg.util import helper, greeter\n\n"
        "def run():\n"
        "    return helper() + greeter()\n"
    ),
    "pkg/cli.py": (
        "from pkg.engine import run\n\n"
        "def main():\n"
        "    return run()\n"
    ),
    "pkg/cycle_a.py": "from pkg.cycle_b import fb\n\ndef fa():\n    return fb()\n",
    "pkg/cycle_b.py": "from pkg.cycle_a import fa\n\ndef fb():\n    return fa()\n",
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


def _snap(conn) -> int:
    return int(conn.execute(
        "SELECT MAX(id) FROM snapshots WHERE status='READY'"
    ).fetchone()[0])


def test_callers_callees_direction(env) -> None:
    conn, _ = env
    snap = _snap(conn)
    g = GraphRetrieval(conn)
    seed = g.seed_occurrences(snap, "pkg.util.helper")
    assert seed
    callees, _ = g.callees(snap, seed[0])
    assert callees == [] or all(h.depth >= 1 for h in callees)
    callers, _ = g.callers(snap, seed[0], limits=TraversalLimits(depth=2))
    names = {h.qualified_name for h in callers}
    assert "pkg.util.greeter" in names
    assert "pkg.engine.run" in names
    # depth=2 alcança cli.main (greeter→run→... não: main chama run)
    callers2, _ = g.callers(snap, seed[0], limits=TraversalLimits(depth=3))
    names2 = {h.qualified_name for h in callers2}
    assert "pkg.cli.main" in names2


def test_cycle_safety_terminates(env) -> None:
    conn, _ = env
    snap = _snap(conn)
    g = GraphRetrieval(conn)
    seed = g.seed_occurrences(snap, "pkg.cycle_a.fa")
    hits, stats = g.traverse(
        snap, seed, direction="both", limits=TraversalLimits(depth=10)
    )
    # ciclo a↔b termina — nunca loop infinito
    names = {h.qualified_name for h in hits}
    assert "pkg.cycle_b.fb" in names
    assert stats.nodes_visited < 10


def test_limits_honored_and_reported(env) -> None:
    conn, _ = env
    snap = _snap(conn)
    g = GraphRetrieval(conn)
    seed = g.seed_occurrences(snap, "pkg.util.helper")
    hits, stats = g.traverse(
        snap, seed, direction="incoming",
        limits=TraversalLimits(depth=4, max_results=1),
    )
    assert len(hits) == 1
    assert stats.truncated_by == "results"


def test_explain_path_per_hit(env) -> None:
    conn, root = env
    res = SearchEngine(conn).search(SearchRequest(
        query="pkg.util.helper", strategy="graph",
        repository_root=root, limit=10,
    ))
    assert res.ranking_version == "graph-structural-v1"
    hit = next(
        h for h in res.items if h.qualified_name == "pkg.engine.run")
    # caminho explicável: evidence traz os saltos de relação
    assert hit.evidence and any(
        "CALLS:" in e or "IMPORTS:" in e or "REFERENCES:" in e
        for e in hit.evidence)


def test_seed_not_found_typed(env) -> None:
    conn, root = env
    res = SearchEngine(conn).search(SearchRequest(
        query="nonexistent.symbol", strategy="graph",
        repository_root=root,
    ))
    assert res.items == ()
    assert any(d.code == "seed-not-found" for d in res.diagnostics)


def test_structural_ranking_prefers_close_resolved(env) -> None:
    conn, _ = env
    snap = _snap(conn)
    g = GraphRetrieval(conn)
    seed = g.seed_occurrences(snap, "pkg.util.helper")
    hits, _ = g.callers(snap, seed[0], limits=TraversalLimits(depth=3))
    depths = [h.depth for h in hits]
    assert depths == sorted(depths)  # distância menor primeiro
