"""lf-0051 — evidence grounding: contratos + validador + grounder."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from code_atlas.indexing import IndexCoordinator
from code_atlas.search.contracts import SearchRequest
from code_atlas.search.engine import SearchEngine
from code_atlas.search.refs import (
    EvidenceGrounder,
    EvidenceValidator,
    OccurrenceRef,
    RelationRef,
)
from code_atlas.storage import connect, migrate

_REPO = {
    "pkg/base.py": "class Base:\n    def load(self):\n        ...\n",
    "pkg/api.py": (
        "from .base import Base\n\n\ndef handle():\n"
        "    return Base()\n"
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
    snap = conn.execute("SELECT id, repository_id FROM snapshots"
                        " WHERE status='READY'").fetchone()
    yield conn, str(repo), int(snap["repository_id"]), int(snap["id"])
    conn.close()


def _occ_ref(env, name: str) -> OccurrenceRef:
    conn, _, repo_id, snap = env
    oid = conn.execute(
        "SELECT o.id FROM symbol_occurrences o JOIN symbols s"
        " ON s.id = o.logical_symbol_id WHERE o.snapshot_id = ?"
        " AND s.qualified_name = ? LIMIT 1", (snap, name)).fetchone()[0]
    ref = EvidenceGrounder(conn).occurrence_ref(repo_id, snap, oid)
    assert ref is not None
    return ref


def test_occurrence_ref_resolves(env) -> None:
    ref = _occ_ref(env, "pkg.base.Base")
    assert ref.symbol_id > 0 and ref.source_path == "pkg/base.py"
    assert ref.content_hash is None or isinstance(ref.content_hash, str)


def test_validator_accepts_real_ref(env) -> None:
    conn, *_ = env
    v = EvidenceValidator(conn)
    assert v.validate_occurrence(_occ_ref(env, "pkg.base.Base")).ok


def test_validator_rejects_cross_repo(env) -> None:
    conn, *_ = env
    ref = replace(_occ_ref(env, "pkg.base.Base"), repository_id=999)
    res = EvidenceValidator(conn).validate_occurrence(ref)
    assert not res.ok and res.code == "cross-repo"


def test_validator_rejects_not_found_and_bad_span(env) -> None:
    conn, *_ = env
    v = EvidenceValidator(conn)
    ref = _occ_ref(env, "pkg.base.Base")
    assert not v.validate_occurrence(
        replace(ref, occurrence_id=999999)).ok
    res = v.validate_occurrence(replace(ref, span_start=99999))
    assert not res.ok and res.code == "bad-span"


def test_relation_ref_validates(env) -> None:
    conn, _, repo_id, snap = env
    row = conn.execute(
        "SELECT id, from_id, to_id, to_unresolved, type,"
        " resolution_status, confidence_class, resolver"
        " FROM relations WHERE snapshot_id = ? AND to_id IS NOT NULL"
        " LIMIT 1", (snap,)).fetchone()
    if row is None:
        pytest.skip("fixture sem aresta resolved")
    ref = RelationRef(
        repository_id=repo_id, snapshot_id=snap,
        relation_id=row["id"], type=row["type"],
        from_occurrence=row["from_id"], to_occurrence=row["to_id"],
        to_unresolved=row["to_unresolved"],
        resolution_status=row["resolution_status"],
        confidence_class=row["confidence_class"],
        resolver=row["resolver"])
    assert EvidenceValidator(conn).validate_relation(ref).ok
    assert not EvidenceValidator(conn).validate_relation(
        replace(ref, snapshot_id=snap + 999)).ok


def test_ground_hit_types_and_no_fabrication(env) -> None:
    conn, root, repo_id, snap = env
    res = SearchEngine(conn).search(SearchRequest(
        query="Base", repository_root=root, strategy="structural"))
    g = EvidenceGrounder(conn)
    hit = res.items[0]
    refs = g.ground_hit(repo_id, snap, hit)
    assert any(r.kind == "syntactic-occurrence" for r in refs)
    # ausência honesta: occurrence inexistente → sem refs de occ
    fake = replace(hit, occurrence_id=987654321)
    refs2 = g.ground_hit(repo_id, snap, fake)
    assert not any(r.kind == "syntactic-occurrence" for r in refs2)
