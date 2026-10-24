"""Testes dos contratos de domínio (vocabulário do Blueprint §5)."""

from __future__ import annotations

import pytest

from code_atlas.contracts import (
    ConfidenceClass,
    EdgeKind,
    Evidence,
    NodeKind,
    Relation,
    ResolutionStatus,
    Snapshot,
    SnapshotStatus,
)


def test_enum_vocabularies_match_blueprint() -> None:
    assert {k.value for k in EdgeKind} == {
        "CONTAINS", "DEFINES", "IMPORTS", "CALLS", "IMPLEMENTS",
        "EXTENDS", "REFERENCES", "CONFIGURES", "TESTS",
    }
    assert {k.value for k in NodeKind} == {
        "repository", "package", "module", "file", "class", "interface",
        "function", "method", "symbol", "config", "unknown",
    }
    assert {k.value for k in ConfidenceClass} == {
        "resolved", "syntactic", "heuristic", "inferred"
    }
    assert {k.value for k in ResolutionStatus} == {
        "resolved", "ambiguous", "unresolved", "external"
    }
    assert {k.value for k in SnapshotStatus} == {
        "BUILDING", "READY", "FAILED", "STALE"
    }


def test_snapshot_status_coercion() -> None:
    s = Snapshot(repository_id=1, worktree_digest="abc", status="READY")  # type: ignore[arg-type]
    assert s.status is SnapshotStatus.READY


def test_snapshot_status_invalid_rejected() -> None:
    with pytest.raises(ValueError, match="invalid status"):
        Snapshot(repository_id=1, worktree_digest="x", status="BROKEN")  # type: ignore[arg-type]


def test_relation_requires_target() -> None:
    with pytest.raises(ValueError, match="to_id or to_unresolved"):
        Relation(
            snapshot_id=1, from_id=1, relation_type=EdgeKind.CALLS,
            resolver="r", confidence_class=ConfidenceClass.RESOLVED,
            resolution_status=ResolutionStatus.RESOLVED,
        )


def test_relation_requires_resolver_provenance() -> None:
    with pytest.raises(ValueError, match="resolver"):
        Relation(
            snapshot_id=1, from_id=1, relation_type=EdgeKind.CALLS, to_id=2,
            resolver="", confidence_class=ConfidenceClass.RESOLVED,
            resolution_status=ResolutionStatus.RESOLVED,
        )


def test_relation_accepts_unresolved_target() -> None:
    r = Relation(
        snapshot_id=1, from_id=1, relation_type="IMPORTS", to_unresolved="lodash",
        resolver="py-imports/0.1", confidence_class="syntactic",
        resolution_status="external",
    )
    assert r.relation_type is EdgeKind.IMPORTS
    assert r.resolution_status is ResolutionStatus.EXTERNAL


def test_evidence_requires_provenance_fields() -> None:
    with pytest.raises(ValueError, match="content_hash"):
        Evidence(snapshot_id=1, source_file_id=1, path="a.py", content_hash="",
                 extractor="x", extractor_version="1")
    with pytest.raises(ValueError, match="extractor"):
        Evidence(snapshot_id=1, source_file_id=1, path="a.py", content_hash="h",
                 extractor="", extractor_version="1")


def test_logical_vs_occurrence_identity() -> None:
    from code_atlas.contracts import LogicalSymbol, SymbolOccurrence

    sym = LogicalSymbol(repository_id=1, language="python",
                        qualified_name="m.f", kind="function")
    occ = SymbolOccurrence(snapshot_id=9, logical_symbol_id=sym.id or 1,
                           source_file_id=2)
    assert sym.id is None  # identidade atribuída pela persistência
    assert occ.snapshot_id == 9 and occ.parse_status == "parsed"
