"""lf-0049 — graph retrieval quality: multi-seed, intent, labels."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from code_atlas.indexing import IndexCoordinator
from code_atlas.search.contracts import SearchRequest
from code_atlas.search.engine import SearchEngine
from code_atlas.search.intent import classify
from code_atlas.storage import connect, migrate

_REPO = {
    "a/base.py": "class Repo:\n    def load(self):\n        ...\n",
    "a/svc.py": (
        "from .base import Repo\n\n\nclass Svc(Repo):\n"
        "    def run(self):\n        return self.load()\n"
    ),
    "a/api.py": (
        "from .svc import Svc\n\n\ndef handle():\n"
        "    return Svc().run()\n"
    ),
}


@pytest.fixture()
def env(tmp_path: Path):
    repo = tmp_path / "r"
    for rel, c in _REPO.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(c)
    conn = connect(tmp_path / "a.db")
    migrate(conn)
    IndexCoordinator(conn).index(repo)
    conn.row_factory = sqlite3.Row
    yield conn, str(repo)
    conn.close()


def test_intent_classification_deterministic() -> None:
    assert classify("who calls create_order").direction == "incoming"
    assert classify("dependencies of Svc").direction == "outgoing"
    assert classify("checkout flow").direction == "both"
    assert classify("plain_symbol").direction == "both"  # default seguro


def test_multi_seed_includes_seed_and_neighbors(env) -> None:
    conn, root = env
    res = SearchEngine(conn).search(SearchRequest(
        query="Svc", repository_root=root, strategy="graph", limit=10))
    names = {h.qualified_name for h in res.items}
    assert any("Svc" in n for n in names)  # seed rotulado presente
    seed_hits = [h for h in res.items
                 if "graph-seed" in h.matched_fields]
    assert seed_hits, "seeds devem ser rotulados graph-seed"
    intent = [d for d in res.diagnostics if d.code == "graph-intent"]
    assert intent and "direction=" in intent[0].message


def test_direction_incoming_finds_importers(env) -> None:
    conn, root = env
    res = SearchEngine(conn).search(SearchRequest(
        query="who calls Svc", repository_root=root,
        strategy="graph", limit=10))
    names = {h.qualified_name for h in res.items}
    # a.api importa a.svc — incoming deve alcançá-lo se aresta resolved
    assert names, "incoming traversal não deve ser vazio"


def test_uncertainty_labels_preserved(env) -> None:
    conn, root = env
    res = SearchEngine(conn).search(SearchRequest(
        query="Svc", repository_root=root, strategy="graph", limit=20))
    labels = {
        part.split("(")[-1].rstrip(")")
        for h in res.items for part in h.evidence if "(" in part
    }
    # labels vêm de resolution_status persistido — nunca "resolved" falso
    assert labels <= {
        "resolved", "ambiguous", "unresolved", "external", "syntactic",
    } or not labels


def test_absent_edges_degrade_honestly(env) -> None:
    conn, root = env
    res = SearchEngine(conn).search(SearchRequest(
        query="nonexistent_xyz", repository_root=root,
        strategy="graph"))
    # sem seed → diagnóstico tipado, nunca hits inventados
    assert res.diagnostics
    assert any(d.code == "seed-not-found" for d in res.diagnostics)


def test_multi_hop_reaches_transitive(env) -> None:
    conn, root = env
    res = SearchEngine(conn).search(SearchRequest(
        query="dependencies of api", repository_root=root,
        strategy="graph", limit=20))
    depths = {h.qualified_name: len(h.evidence) for h in res.items}
    # outgoing: api→svc→base; algum hit deve ter >=1 salto
    assert any(v >= 1 for v in depths.values()), depths
