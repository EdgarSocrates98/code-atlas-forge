"""lf-0058 Phase 0.3 — EvidenceValidator rejeita metadados adulterados."""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "golden"))

from retrieval_eval import build_eval_repo

from code_atlas.search.refs import (
    EvidenceGrounder,
    EvidenceValidator,
    SnapshotRef,
)


@pytest.fixture()
def env(tmp_path):
    root = tmp_path / "repo"
    conn = build_eval_repo(root)
    yield conn
    conn.close()


def _refs(conn):
    g = EvidenceGrounder(conn)
    occ = g.occurrence_ref(1, 1, 1)
    assert occ is not None
    rel = conn.execute(
        "SELECT id FROM relations WHERE snapshot_id=1 LIMIT 1"
    ).fetchone()
    return occ, rel


def test_valid_occurrence_passes(env) -> None:
    occ, _ = _refs(env)
    assert EvidenceValidator(env).validate_occurrence(occ).ok


@pytest.mark.parametrize(
    "field,bad,code",
    [
        ("repository_id", 999, "cross-repo"),
        ("snapshot_id", 999, "not-found"),
        ("symbol_id", 999999, "metadata-mismatch"),
        ("qualified_name", "evil.name", "metadata-mismatch"),
        ("span_start", -1, "bad-span"),
        ("span_end", 99999, "bad-span"),
        ("content_hash", "deadbeef", "metadata-mismatch"),
        ("parse_status", "forged", "metadata-mismatch"),
        ("source_path", "other/file.py", "cross-snapshot"),
    ],
)
def test_tampered_occurrence_rejected(env, field, bad, code) -> None:
    occ, _ = _refs(env)
    res = EvidenceValidator(env).validate_occurrence(
        dataclasses.replace(occ, **{field: bad}))
    assert not res.ok and res.code == code


def test_relation_metadata_tampered(env) -> None:
    _, rel = _refs(env)
    if rel is None:
        pytest.skip("fixture sem relations")
    row = env.execute(
        "SELECT * FROM relations WHERE id=?", (rel["id"],)).fetchone()
    from code_atlas.search.refs import RelationRef
    ref = RelationRef(
        repository_id=1, snapshot_id=1, relation_id=int(row["id"]),
        type=str(row["type"]), from_occurrence=int(row["from_id"]),
        to_occurrence=row["to_id"], to_unresolved=row["to_unresolved"],
        resolution_status=str(row["resolution_status"]),
        confidence_class=str(row["confidence_class"]),
        resolver=row["resolver"])
    v = EvidenceValidator(env)
    assert v.validate_relation(ref).ok
    for field in ("type", "resolution_status", "confidence_class"):
        res = v.validate_relation(
            dataclasses.replace(ref, **{field: "tampered"}))
        assert not res.ok and res.code == "metadata-mismatch", field
    res = v.validate_relation(
        dataclasses.replace(ref, to_occurrence=999999))
    assert not res.ok


def test_snapshot_ref_cross_repo(env) -> None:
    v = EvidenceValidator(env)
    assert v.validate_snapshot(SnapshotRef(1, 1)).ok
    res = v.validate_snapshot(SnapshotRef(2, 1))
    assert not res.ok and res.code == "cross-repo"
    res = v.validate_snapshot(SnapshotRef(1, 999))
    assert not res.ok and res.code == "not-found"


def test_symbol_ref_tampered(env) -> None:
    from code_atlas.search.refs import SymbolRef
    row = env.execute(
        "SELECT id, qualified_name, kind, language FROM symbols"
        " LIMIT 1").fetchone()
    v = EvidenceValidator(env)
    ok = SymbolRef(1, int(row["id"]), str(row["qualified_name"]),
                   str(row["kind"]), str(row["language"]))
    assert v.validate_symbol(ok, 1).ok
    bad = dataclasses.replace(ok, kind="forged-kind")
    res = v.validate_symbol(bad, 1)
    assert not res.ok and res.code == "metadata-mismatch"
