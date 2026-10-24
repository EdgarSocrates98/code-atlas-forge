"""lf-0038 — hybrid search + RRF: fusão, dedup com evidência,
degradação sem vetorial, comparativo contra baselines."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from code_atlas.indexing import IndexCoordinator
from code_atlas.search import SearchEngine, SearchRequest
from code_atlas.search.contracts import SearchHit
from code_atlas.search.hybrid import RRF_K, fuse, to_search_hit
from code_atlas.storage import connect, migrate

_REPO = {
    "pkg/__init__.py": "",
    "pkg/util.py": "def helper():\n    return 1\n",
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


def _hit(occ_id: int, name: str = "", strategy: str = "lexical"):
    return SearchHit(
        occurrence_id=occ_id, qualified_name=name or f"s{occ_id}",
        kind="function", language="python", path="p.py", span=None,
        score=1.0, strategy=strategy,
    )


def test_rrf_math_and_determinism() -> None:
    a = [_hit(1), _hit(2), _hit(3)]
    b = [_hit(2), _hit(1)]
    fused = fuse({"lexical": a, "graph": b})
    s1 = 1 / (RRF_K + 1) + 1 / (RRF_K + 2)
    s3 = 1 / (RRF_K + 3)
    by = {c.symbol_key: c for c in fused}
    assert abs(by["s1"].rrf_score - s1) < 1e-9
    assert abs(by["s2"].rrf_score - s1) < 1e-9
    assert abs(by["s3"].rrf_score - s3) < 1e-9
    # determinístico: mesma entrada → mesma ordem
    assert [c.symbol_key for c in fuse({"a": a, "b": b})] == [
        c.symbol_key for c in fuse({"a": a, "b": b})]


def test_dedup_preserves_each_strategy_evidence() -> None:
    a = [_hit(1, strategy="lexical"), _hit(9, strategy="lexical")]
    b = [_hit(1, strategy="graph")]
    fused = fuse({"lexical": a, "graph": b})
    c1 = next(c for c in fused if c.symbol_key == "s1")
    assert set(c1.ranks) == {"lexical", "graph"}
    assert len(c1.hits) == 2
    h = to_search_hit(c1)
    assert "lexical:rank=1" in h.evidence[0] or any(
        e.startswith("lexical:rank=") for e in h.evidence)
    assert any(e.startswith("rrf=") for e in h.evidence)
    # hit primário é o de melhor rank; o outro não é descartado
    assert {x.strategy for x in c1.hits} == {"lexical", "graph"}


def test_hybrid_end_to_end(env) -> None:
    conn, root = env
    res = SearchEngine(conn).search(SearchRequest(
        query="helper", strategy="hybrid", repository_root=root,
        limit=10,
    ))
    assert res.ranking_version == "rrf-k60-v1"
    names = [h.qualified_name for h in res.items]
    assert "pkg.util.helper" in names
    # matched_fields lista estratégias participantes por hit
    top = res.items[0]
    assert set(top.matched_fields) <= {"lexical", "structural", "graph"}


def test_hybrid_works_without_vector(env) -> None:
    """Degradação honesta: 3 estratégias reais, sem provider vetorial."""
    conn, root = env
    caps = SearchEngine(conn).capabilities()
    assert "hybrid" in caps.strategies and not caps.vector
    res = SearchEngine(conn).search(SearchRequest(
        query="helper", strategy="hybrid", repository_root=root))
    assert res.items  # funciona sem vetorial


def test_hybrid_vs_baselines_union_coverage(env) -> None:
    """Medição no fixture: hybrid cobre >= o que cada baseline cobre."""
    conn, root = env
    eng = SearchEngine(conn)
    lex = {h.qualified_name for h in eng.search(SearchRequest(
        query="helper", strategy="lexical", repository_root=root,
        limit=20)).items}
    st = {h.qualified_name for h in eng.search(SearchRequest(
        query="helper", strategy="structural", repository_root=root,
        limit=20)).items}
    hy = {h.qualified_name for h in eng.search(SearchRequest(
        query="helper", strategy="hybrid", repository_root=root,
        limit=20)).items}
    assert lex <= hy and st <= hy  # união dos baselines preservada
