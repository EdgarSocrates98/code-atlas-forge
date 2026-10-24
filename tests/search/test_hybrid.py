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
    by = {c.qualified_name: c for c in fused}
    assert abs(by["s1"].rrf_score - s1) < 1e-9
    assert abs(by["s2"].rrf_score - s1) < 1e-9
    assert abs(by["s3"].rrf_score - s3) < 1e-9
    # determinístico: mesma entrada → mesma ordem
    assert [c.symbol_key for c in fuse({"a": a, "b": b})] == [
        c.symbol_key for c in fuse({"a": a, "b": b})]
    assert all(c.symbol_key == f"python:function:{c.qualified_name}"
               for c in fused)  # chave canônica composta (lf-0050)


def test_dedup_preserves_each_strategy_evidence() -> None:
    a = [_hit(1, strategy="lexical"), _hit(9, strategy="lexical")]
    b = [_hit(1, strategy="graph")]
    fused = fuse({"lexical": a, "graph": b})
    c1 = next(c for c in fused if c.qualified_name == "s1")
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
    assert res.ranking_version == "rrf-k60-v2"
    names = [h.qualified_name for h in res.items]
    assert "pkg.util.helper" in names
    # matched_fields lista estratégias participantes por hit
    top = res.items[0]
    assert set(top.matched_fields) <= {
        "lexical", "structural", "graph", "vector"}


def test_hybrid_works_without_vector(env) -> None:
    """Degradação honesta: vetorial desligado, hybrid sobre 3 reais."""
    conn, root = env
    caps = SearchEngine(conn, enable_vector=False).capabilities()
    assert "hybrid" in caps.strategies and not caps.vector
    res = SearchEngine(conn, enable_vector=False).search(SearchRequest(
        query="helper", strategy="hybrid", repository_root=root))
    assert res.items  # funciona sem vetorial
    assert all(
        "vector" not in h.matched_fields for h in res.items)


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


def test_canonical_key_separates_same_name_diff_lang() -> None:
    """lf-0050 — mesma qname, linguagens diferentes NÃO fundem."""
    py = _hit(1, name="app.main")
    ts = SearchHit(
        occurrence_id=2, qualified_name="app.main", kind="function",
        language="typescript", path="p.ts", span=None, score=1.0,
        strategy="lexical")
    fused = fuse({"lexical": [py, ts]})
    assert len(fused) == 2  # não funde identidades diferentes
    assert {c.qualified_name for c in fused} == {"app.main"}


def test_rerank_exact_qname_boosts() -> None:
    """lf-0050 — boost documentado de exact-qname dispara e explica."""
    weak = _hit(1, name="pkg.util.helper")
    strong = _hit(2, name="other.target")
    # 'target' em segundo rank de cada estratégia, helper no topo só de uma
    fused = fuse(
        {"lexical": [weak, strong], "graph": [weak, strong]},
        query="target")
    top = fused[0]
    assert top.qualified_name == "other.target"  # boost venceu rank puro
    assert any("exact-qname" in r for r in top.rerank_reasons)
    h = to_search_hit(top)
    assert any(e.startswith("rerank=") for e in h.evidence)


def test_rerank_off_is_pure_rrf() -> None:
    fused = fuse({"lexical": [_hit(1, name="a.t")], "graph": [_hit(2)]})
    # sem intent/query: sem boosts task-aware — só demote de evidência
    assert not any("exact-qname" in r or "direction=" in r
                   for r in fused[0].rerank_reasons)
