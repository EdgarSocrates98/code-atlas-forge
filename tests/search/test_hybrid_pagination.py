"""lf-0058 Phase 0.2 — paginação determinística da busca híbrida."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "golden"))

from retrieval_eval import build_eval_repo

from code_atlas.search.contracts import SearchRequest
from code_atlas.search.engine import SearchEngine


@pytest.fixture()
def env(tmp_path):
    root = tmp_path / "repo"
    conn = build_eval_repo(root)
    yield conn, str(root.resolve())
    conn.close()


def _search(conn, root, **kw):
    return SearchEngine(conn).search(
        SearchRequest(query=kw.pop("query", "e"), strategy="hybrid",
                      repository_root=root, **kw))


def test_hybrid_respects_limit(env) -> None:
    conn, root = env
    res = _search(conn, root, limit=1)
    assert len(res.items) <= 1
    res10 = _search(conn, root, limit=10)
    assert len(res10.items) <= 10


def test_hybrid_max_limit_clamped(env) -> None:
    conn, root = env
    res = _search(conn, root, limit=10_000)
    assert res.limit <= 200
    assert len(res.items) <= 200


def test_hybrid_pages_have_no_duplicates(env) -> None:
    conn, root = env
    p1 = _search(conn, root, limit=2)
    assert p1.truncated and p1.next_cursor
    p2 = _search(conn, root, limit=2, cursor=p1.next_cursor)
    ids1 = {h.occurrence_id for h in p1.items}
    ids2 = {h.occurrence_id for h in p2.items}
    assert not (ids1 & ids2)
    # última página: sem cursor, truncated False
    seen, cur, last = set(), p1.next_cursor, p2
    while last.truncated and last.next_cursor:
        cur = last.next_cursor
        last = _search(conn, root, limit=2, cursor=cur)
        assert not last.items or not (
            {h.occurrence_id for h in last.items} & seen)
        seen |= {h.occurrence_id for h in last.items}
    assert last.truncated is False


def test_hybrid_cursor_invalid(env) -> None:
    conn, root = env
    res = _search(conn, root, cursor="bogus!!")
    assert any(d.code == "invalid-cursor" for d in res.diagnostics)
    assert res.items == ()


def test_hybrid_cursor_bound_to_query(env) -> None:
    conn, root = env
    p1 = _search(conn, root, query="a", limit=1)
    res = _search(conn, root, query="zz_other", limit=1,
                  cursor=p1.next_cursor)
    assert any(d.code == "invalid-cursor" for d in res.diagnostics)


def test_hybrid_cursor_bound_to_snapshot(env) -> None:
    conn, root = env
    from code_atlas.indexing import IndexCoordinator
    p1 = _search(conn, root, limit=1)
    assert p1.next_cursor
    # novo snapshot (mesmo conteúdo → no-op? força com arquivo novo)
    (Path(root) / "pkg" / "extra.py").write_text(
        "def extra_fn():\n    return 1\n")
    IndexCoordinator(conn).index(Path(root))
    res = _search(conn, root, limit=1, cursor=p1.next_cursor)
    assert any(d.code == "invalid-cursor" for d in res.diagnostics)


def test_hybrid_total_items_bounded(env) -> None:
    conn, root = env
    res = _search(conn, root, limit=3)
    # nunca devolve mais que o pedido — bug P1 original
    assert len(res.items) <= 3
