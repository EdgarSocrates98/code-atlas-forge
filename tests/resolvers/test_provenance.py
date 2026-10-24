"""lf-0028 — proveniência + revision safety de índice SCIP.

Todo cenário de rejeição/degradação tem diagnóstico tipado; índice
incompatível nunca contamina snapshot READY.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from scip_fixture import (
    document,
    index_bytes,
    occurrence,
    slr,
    symbol_info,
    tool_info,
)

from code_atlas.resolvers.contracts import ResolutionRequest
from code_atlas.resolvers.scip.importer import (
    ROLE_DEFINITION,
    parse_index,
)
from code_atlas.resolvers.scip.provenance import (
    STATUS_CURRENT,
    STATUS_DEGRADED,
    STATUS_REJECTED,
    IndexProvenance,
    assess_revision,
)
from code_atlas.resolvers.scip.resolver import ScipResolver

PKG = "scip-python . repo 0.1.0"
SYM = f"{PKG} `pkg/util.py`/helper()."


def _repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / "pkg").mkdir(parents=True)
    (r / "pkg" / "util.py").write_text(
        "def helper():\n    return 1\n")
    return r


def _scip(tmp_path: Path, repo: Path, **kw: object) -> Path:
    doc = document(
        "pkg/util.py",
        occs=[occurrence(SYM, slr(0, 4, 10), roles=ROLE_DEFINITION)],
        syms=[symbol_info(SYM, kind=17)],
    )
    f = tmp_path / "index.scip"
    f.write_bytes(index_bytes(repo.as_uri(), [doc], **kw))  # type: ignore[arg-type]
    return f


def test_current_index_full_provenance(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    scip = _scip(tmp_path, repo)
    idx = parse_index(scip.read_bytes())
    a = assess_revision(idx, scip, repo)
    p = a.provenance
    assert p.validation_status == STATUS_CURRENT
    assert p.index_format == "scip"
    assert len(p.index_sha256) == 64
    assert p.producer_name == "scip-python"
    assert p.producer_version == "0.11.0"
    assert p.repository_identity == repo.as_uri()
    assert p.worktree_digest  # digest sobre arquivos indexados
    assert p.indexed_at      # mtime do .scip (evidência host)
    assert "project_root matches workspace" in p.validation_evidence
    assert not a.errors


def test_cross_workspace_rejected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    scip = _scip(tmp_path, repo)
    idx = parse_index(scip.read_bytes())
    a = assess_revision(idx, scip, other)
    assert a.provenance.validation_status == STATUS_REJECTED
    assert a.errors and "project_root" in a.errors[0]


def test_missing_indexed_file_degraded(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _ = repo
    scip = _scip(tmp_path, repo)
    (repo / "pkg" / "util.py").unlink()  # arquivo removido após o índice
    a = assess_revision(parse_index(scip.read_bytes()), scip, repo)
    assert a.provenance.validation_status == STATUS_DEGRADED
    assert any("indexed file missing" in w for w in a.warnings)


def test_file_newer_than_index_degraded(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    scip = _scip(tmp_path, repo)
    # arquivo modificado DEPOIS do índice → stale
    future = os.stat(scip).st_mtime + 10
    os.utime(repo / "pkg" / "util.py", (future, future))
    a = assess_revision(parse_index(scip.read_bytes()), scip, repo)
    assert a.provenance.validation_status == STATUS_DEGRADED
    assert any("newer than index" in w for w in a.warnings)


def test_span_beyond_file_end_degraded(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    doc = document(
        "pkg/util.py",
        occs=[occurrence(SYM, slr(999, 0, 5), roles=ROLE_DEFINITION)],
    )
    f = tmp_path / "index.scip"
    f.write_bytes(index_bytes(repo.as_uri(), [doc]))
    a = assess_revision(parse_index(f.read_bytes()), f, repo)
    assert a.provenance.validation_status == STATUS_DEGRADED
    assert any("beyond file end" in w for w in a.warnings)


def test_missing_tool_version_degraded(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    # producer sem versão → evidência de revisão insuficiente
    scip = _scip(tmp_path, repo,
                 tool=tool_info("scip-python", ""))
    a = assess_revision(parse_index(scip.read_bytes()), scip, repo)
    assert a.provenance.validation_status == STATUS_DEGRADED
    assert any("tool_info.version missing" in w for w in a.warnings)


def test_non_normalized_path_degraded(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    doc = document("pkg\\util.py")  # backslash — path manipulado
    f = tmp_path / "index.scip"
    f.write_bytes(index_bytes(repo.as_uri(), [doc]))
    a = assess_revision(parse_index(f.read_bytes()), f, repo)
    assert a.provenance.validation_status == STATUS_DEGRADED
    assert any("non-normalized path" in w for w in a.warnings)


def test_duplicate_symbol_across_docs_degraded(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "pkg" / "dup.py").write_text("x = 1\n")
    doc_a = document("pkg/util.py",
                     syms=[symbol_info(SYM, kind=17)])
    doc_b = document("pkg/dup.py",
                     syms=[symbol_info(SYM, kind=17)])
    f = tmp_path / "index.scip"
    f.write_bytes(index_bytes(repo.as_uri(), [doc_a, doc_b]))
    a = assess_revision(parse_index(f.read_bytes()), f, repo)
    assert a.provenance.validation_status == STATUS_DEGRADED
    assert any("defined in" in w for w in a.warnings)


def test_untrusted_position_encoding_degraded(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    doc = document("pkg/util.py")
    # position_encoding é o field 6 do Document — fixture fixa 3;
    # reconstruo com encoding=2 (UTF-16, não suportado pelo mapper).
    from scip_fixture import f_msg, f_string, f_varint
    raw_doc = (f_string(1, "pkg/util.py") + f_string(4, "python")
               + f_varint(6, 2))
    doc_bytes = f_msg(2, raw_doc) if False else raw_doc
    _ = doc  # fixture-based doc não usado — raw acima é o alvo
    idx = index_bytes(repo.as_uri(), [doc_bytes])
    f = tmp_path / "index.scip"
    f.write_bytes(idx)
    a = assess_revision(parse_index(idx), f, repo)
    assert a.provenance.validation_status == STATUS_DEGRADED
    assert any("position_encoding" in w for w in a.warnings)


def test_corrupted_index_no_provenance(tmp_path: Path) -> None:
    _repo(tmp_path)
    f = tmp_path / "index.scip"
    f.write_bytes(b"\xff\xff\xff")
    from code_atlas.resolvers.scip.importer import DecodeError
    with pytest.raises(DecodeError):
        parse_index(f.read_bytes())


def test_unreadable_index_rejected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    a = assess_revision(
        parse_index(index_bytes(repo.as_uri(), [])),
        tmp_path / "nonexistent.scip",
        repo,
        raw_bytes=None,
    )
    assert a.provenance.validation_status == STATUS_REJECTED


# -- efeito no resolver ---------------------------------------------------


def _universe(tmp_path: Path) -> object:
    from code_atlas.discovery.scanner import scan
    from code_atlas.graph.builder import GraphBuilder
    from code_atlas.parsers import ParseEngine

    repo = _repo(tmp_path)
    s = scan(repo)
    results = ParseEngine().parse_batch(
        [(f.path, (repo / f.path).read_bytes()) for f in s.files])
    b = GraphBuilder()
    b.build("repo", s, results)
    return b.universe


def test_degraded_never_promotes_as_proven(tmp_path: Path) -> None:
    """Índice degraded resolve mas capa confidence — relação degradada
    nunca vira 'comprovada'."""
    uni = _universe(tmp_path)
    prov = IndexProvenance(
        producer_name="scip-python",
        validation_status=STATUS_DEGRADED,
        validation_evidence=("stale",),
    )
    idx = parse_index(
        index_bytes("file:///repo", [
            document("pkg/util.py",
                     occs=[occurrence(
                         SYM, slr(0, 4, 10), roles=ROLE_DEFINITION)]),
        ]))
    r = ScipResolver(idx, uni, prov)
    req = ResolutionRequest(name="helper", kind="call",
                            path="pkg/util.py")
    res = r.resolve_symbol(req, SYM)
    assert res.reference is not None
    if res.reference.status == "resolved":
        assert res.reference.confidence_class == "heuristic"
        assert "[degraded]" in res.reference.provenance.detail


def test_rejected_index_never_resolves(tmp_path: Path) -> None:
    uni = _universe(tmp_path)
    prov = IndexProvenance(
        validation_status=STATUS_REJECTED,
        validation_evidence=("project_root mismatch",),
    )
    r = ScipResolver(parse_index(index_bytes("file:///repo", [])),
                     uni, prov)
    res = r.resolve(
        ResolutionRequest(name="x", kind="call", path="pkg/util.py"))
    assert res.reference is not None
    assert res.reference.status == "unresolved"
    assert res.diagnostics[0].code == "index-rejected"
    assert res.diagnostics[0].severity == "error"


def test_no_assessment_is_degraded_by_default(tmp_path: Path) -> None:
    """Sem assessment explícito, índice é não-verificado → degraded."""
    uni = _universe(tmp_path)
    r = ScipResolver(
        parse_index(index_bytes("file:///repo", [])), uni)
    assert r.provenance.validation_status == STATUS_DEGRADED
    assert "no revision assessment" in r.provenance.validation_evidence


def test_provenance_summary_inspectable() -> None:
    p = IndexProvenance(
        index_sha256="a" * 64,
        producer_name="scip-python",
        producer_version="0.11.0",
        validation_status=STATUS_CURRENT,
    )
    s = p.summary()
    assert "current" in s and "scip-python" in s and "unknown" in s
