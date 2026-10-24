"""lf-0039 — busca vetorial opt-in: provider naïve, cache por conteúdo,
reindex por modelo, backend honesto, fusão híbrida."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from code_atlas.indexing import IndexCoordinator
from code_atlas.search import SearchEngine, SearchRequest
from code_atlas.search.vector import (
    BruteForceIndex,
    NaiveEmbeddingProvider,
    SqliteEmbeddingCache,
    content_hash,
    embed_texts_cached,
)
from code_atlas.storage import connect, migrate

_REPO = {
    "pkg/__init__.py": "",
    "pkg/util.py": 'def helper():\n    """Utility helper."""\n    return 1\n',
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
    yield conn, str(repo)
    conn.close()


def _search(env, q: str, strategy: str = "vector"):
    conn, root = env
    return SearchEngine(conn).search(SearchRequest(
        query=q, strategy=strategy, repository_root=root, limit=10))


def test_naive_provider_deterministic() -> None:
    p = NaiveEmbeddingProvider(dim=64)
    a = p.embed(["def helper(): return 1"])
    b = p.embed(["def helper(): return 1"])
    assert a == b and len(a[0]) == 64
    # L2-normalizado
    assert abs(sum(x * x for x in a[0]) - 1.0) < 1e-6
    # textos diferentes → vetores diferentes
    assert p.embed(["alpha beta"]) != p.embed(["gamma delta"])


def test_cache_by_content_and_model() -> None:
    conn = connect(Path(":memory:"))
    migrate(conn)
    conn.row_factory = sqlite3.Row
    cache = SqliteEmbeddingCache(conn)
    p = NaiveEmbeddingProvider(dim=32)
    texts = ["def helper(): return 1"]
    _, hits, misses = embed_texts_cached(p, cache, texts)
    assert (hits, misses) == (0, 1)
    _, hits, misses = embed_texts_cached(p, cache, texts)
    assert (hits, misses) == (1, 0)  # conteúdo igual → cache hit


def test_bruteforce_index_cosine(env) -> None:
    conn, _root = env
    idx = BruteForceIndex(conn, "m", "v1")
    idx.upsert(1, 1, "a.b.c", [1.0, 0.0, 0.0])
    idx.upsert(1, 2, "a.b.d", [0.0, 1.0, 0.0])
    conn.commit()
    res = idx.search(1, [0.9, 0.1, 0.0], 5)
    assert res[0].occurrence_id == 1 and res[0].score > res[1].score
    # isolamento por snapshot: snapshot 2 não vê vetores do 1
    assert idx.search(2, [1.0, 0.0, 0.0], 5) == []


def test_vector_strategy_end_to_end(env) -> None:
    res = _search(env, "helper")
    assert res.ranking_version == "vector-cosine-v1"
    assert res.items and res.items[0].qualified_name.endswith("helper")
    top = res.items[0]
    assert "embedding" in top.matched_fields
    assert any(e.startswith("cosine=") for e in top.evidence)
    # score 0 (sem similaridade) não polui — ortogonais filtrados
    assert all(h.score > 0 for h in res.items)


def test_capability_honest(env) -> None:
    conn, _ = env
    caps = SearchEngine(conn).capabilities()
    assert caps.vector is True
    assert "vector" in caps.strategies
    # backend declarado (bruteforce no CI, sqlite-vec se extra ativo)
    assert "bruteforce" in caps.detail or "sqlite-vec" in caps.detail
    caps_off = SearchEngine(conn, enable_vector=False).capabilities()
    assert caps_off.vector is False
    assert "vector" not in caps_off.strategies


def test_disabled_engine_strategies_unaffected(env) -> None:
    conn, root = env
    eng = SearchEngine(conn, enable_vector=False)
    for strategy in ("lexical", "structural", "graph", "hybrid"):
        q = "helper" if strategy != "graph" else "pkg.util.helper"
        res = eng.search(SearchRequest(
            query=q, strategy=strategy, repository_root=root, limit=10))
        assert res.items, strategy
    res = eng.search(SearchRequest(
        query="helper", strategy="vector", repository_root=root))
    # vetorial desligada ainda responde (provider interno default) —
    # desligar só a remove da fusão/capabilities, nunca quebra
    assert res.ranking_version in ("vector-cosine-v1",)


def test_hybrid_includes_vector_ranking(env) -> None:
    res = _search(env, "helper", strategy="hybrid")
    by = {h.qualified_name: h for h in res.items}
    helper = by.get("pkg.util.helper")
    assert helper is not None
    # helper aparece em lexical/structural/vector — evidência preservada
    assert "vector" in helper.matched_fields or len(
        helper.matched_fields) >= 2
    assert any(e.startswith("vector:rank=") for e in helper.evidence)


def test_reindex_on_model_change(env) -> None:
    """Modelo mudou de versão → embeddings antigas não casam → rebuild."""
    conn, _root = env
    _search(env, "helper")
    n1 = conn.execute(
        "SELECT COUNT(*) AS c FROM vector_embeddings").fetchone()["c"]
    assert n1 > 0
    # versão nova do modelo → ensure reindexa (mesma ocorrência, hash
    # de conteúdo igual MAS model_version diferente)
    conn.execute(
        "UPDATE vector_embeddings SET model_version = 'v0-OLD'")
    conn.commit()
    res = _search(env, "helper")
    assert res.items  # rebuilt transparentemente
    rows = conn.execute(
        "SELECT DISTINCT model_version FROM vector_embeddings"
    ).fetchall()
    # linhas atualizadas para a versão corrente (INSERT OR REPLACE)
    assert any(r["model_version"] == "v1" for r in rows)


def test_embedding_cache_table_versioned(env) -> None:
    conn, _ = env
    cache = SqliteEmbeddingCache(conn)
    p = NaiveEmbeddingProvider(dim=16)
    h = content_hash("texto")
    cache.put(p.info(), h, [0.5] * 16)
    assert cache.get(p.info(), h) == [0.5] * 16
    # modelo "antigo" não lê versão nova (versionamento estrutural)
    old = p.info().__class__(
        model_id="naive-hash", version="v0", dim=16)
    assert cache.get(old, h) is None
