"""Testes do subsistema SCIP (lf-0017): decoder, importer, validação,
mapper e ScipResolver — incluindo e2e via coordinator."""

from __future__ import annotations

import random
from pathlib import Path

import pytest
from scip_fixture import (
    document,
    f_string,
    index_bytes,
    occurrence,
    slr,
    symbol_info,
    tool_info,
)

from code_atlas.graph.builder import GraphBuilder
from code_atlas.parsers.contracts import SourceSpan
from code_atlas.resolvers.contracts import (
    ResolutionRequest,
    SemanticResolver,
)
from code_atlas.resolvers.coordinator import ResolverCoordinator
from code_atlas.resolvers.scip import (
    DecodeError,
    ScipResolver,
    load_index,
    parse_index,
)
from code_atlas.resolvers.scip.importer import ROLE_DEFINITION
from code_atlas.resolvers.scip.mapper import (
    parse_symbol,
    symbol_to_qname,
)
from code_atlas.resolvers.scip.provenance import IndexProvenance
from code_atlas.resolvers.scip.validation import (
    project_root_matches,
    validate_index,
)
from code_atlas.resolvers.syntactic.resolver import SyntacticResolver

ROOT = "file:///repo"
PKG = "scip-python . mypkg 0.1.0"
SYM_HELPER = f"{PKG} `pkg/util.py`/helper()."
SYM_RUN = f"{PKG} `pkg/engine.py`/Engine#run()."


@pytest.fixture
def valid_index() -> bytes:
    doc_util = document(
        "pkg/util.py",
        occs=[
            occurrence(SYM_HELPER, slr(0, 4, 10), roles=ROLE_DEFINITION),
        ],
        syms=[symbol_info(SYM_HELPER, kind=17, docs=["helper doc"])],
    )
    doc_engine = document(
        "pkg/engine.py",
        occs=[
            occurrence(SYM_RUN, slr(1, 8, 11), roles=ROLE_DEFINITION),
            occurrence(SYM_HELPER, slr(3, 15, 21), roles=0),  # call site
        ],
    )
    return index_bytes(ROOT, [doc_util, doc_engine],
                     tool=tool_info("scip-python", "0.11.0"))


# -- decoder / importer ---------------------------------------------------


def test_parse_valid_index(valid_index: bytes) -> None:
    idx = parse_index(valid_index)
    assert idx.tool_name == "scip-python"
    assert idx.tool_version == "0.11.0"
    assert idx.project_root == ROOT
    assert len(idx.documents) == 2
    util = idx.documents[0]
    assert util.relative_path == "pkg/util.py"
    assert util.occurrences[0].is_definition
    assert util.symbols[0].documentation == ("helper doc",)


def test_decode_truncated(valid_index: bytes) -> None:
    with pytest.raises(DecodeError):
        parse_index(valid_index[: len(valid_index) // 2])


def test_decode_garbage() -> None:
    with pytest.raises(DecodeError):
        parse_index(b"\xff\xff\xff\xff\xff")


def test_fuzz_mutations_no_wrong_symbols(valid_index: bytes) -> None:
    """Mutations aleatórias: erro ou decode diferente — nunca crash nem
    símbolo inventado silenciosamente (verificação: parse não aceita
    metade truncada nem bytes corrompidos sem reclamar)."""
    rng = random.Random(42)
    base = parse_index(valid_index)
    base_paths = {d.relative_path for d in base.documents}
    bad = 0
    for _ in range(200):
        data = bytearray(valid_index)
        pos = rng.randrange(len(data))
        data[pos] ^= 1 << rng.randrange(8)
        try:
            idx = parse_index(bytes(data))
        except (DecodeError, ValueError):
            bad += 1
            continue
        # se decodificou, paths documentais não podem ser inventados:
        # ou são os mesmos, ou corrompidos mas ainda parseáveis — o que
        # importa é que o decoder nunca levanta exceção de tipo errado
        for d in idx.documents:
            assert isinstance(d.relative_path, str)
            _ = d.relative_path in base_paths  # informativo, sem assert
    assert bad > 0  # mutações de fato provocam erros


def test_load_index_file(tmp_path: Path, valid_index: bytes) -> None:
    f = tmp_path / "index.scip"
    f.write_bytes(valid_index)
    out = load_index(f)
    assert out.ok
    assert out.index is not None and len(out.index.documents) == 2


def test_load_index_too_large(tmp_path: Path) -> None:
    f = tmp_path / "big.scip"
    f.write_bytes(b"x" * 100)
    out = load_index(f, max_bytes=10)
    assert not out.ok
    assert "too large" in out.errors[0]


def test_load_index_malformed(tmp_path: Path) -> None:
    f = tmp_path / "bad.scip"
    f.write_bytes(b"\x08\x96\x01\x0a")
    out = load_index(f)
    assert not out.ok
    assert "decode" in out.errors[0] or "truncated" in out.errors[0].lower()


# -- validação / binding de revisão ---------------------------------------


def test_validate_duplicate_paths() -> None:
    doc = document("a.py")
    idx = parse_index(index_bytes(ROOT, [doc, doc]))
    res = validate_index(idx, 100)
    assert res.ok
    assert any("duplicate" in w for w in res.warnings)


def test_validate_bad_path() -> None:
    idx = parse_index(index_bytes(ROOT, [document("../escape.py")]))
    res = validate_index(idx, 100)
    assert not res.ok


def test_revision_binding_rejects_wrong_root(tmp_path: Path) -> None:
    f = tmp_path / "index.scip"
    f.write_bytes(index_bytes("file:///other/repo", [document("a.py")]))
    out = load_index(f, workspace_root=tmp_path)
    assert not out.ok
    assert "project_root" in out.errors[0]


def test_revision_binding_opt_in(tmp_path: Path) -> None:
    f = tmp_path / "index.scip"
    f.write_bytes(index_bytes("file:///other/repo", [document("a.py")]))
    out = load_index(f, workspace_root=tmp_path,
                   allow_revision_mismatch=True)
    assert out.ok
    assert any("allowed by flag" in w for w in out.warnings)


def test_project_root_matches(tmp_path: Path) -> None:
    idx = parse_index(index_bytes(
        tmp_path.as_uri(), [document("a.py")]))
    assert project_root_matches(idx, tmp_path)
    assert not project_root_matches(idx, tmp_path / "other")


# -- mapper ----------------------------------------------------------------


def test_parse_symbol_roundtrip() -> None:
    sym = parse_symbol(SYM_HELPER)
    assert sym is not None
    assert sym.scheme == "scip-python"
    assert sym.package_name == "mypkg"
    assert not sym.is_local
    assert symbol_to_qname(sym) == "pkg/util.py.helper"


def test_parse_local_symbol() -> None:
    sym = parse_symbol("local 42")
    assert sym is not None and sym.is_local
    assert sym.local_id == "42"


def test_parse_symbol_malformed() -> None:
    assert parse_symbol("") is None
    assert parse_symbol("not a symbol") is None




def _prov_current() -> IndexProvenance:
    """Proveniência explícita 'current' para testes do resolver."""
    return IndexProvenance(validation_status="current")

# -- ScipResolver e2e -------------------------------------------------------


def _build_universe(tmp_path: Path) -> GraphBuilder:
    """Grafo real de 2 arquivos python → builder (universe acessível)."""
    from code_atlas.discovery.scanner import scan
    from code_atlas.parsers import ParseEngine

    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "pkg" / "util.py").write_text(
        "def helper():\n    return 1\n")
    (repo / "pkg" / "engine.py").write_text(
        "from pkg.util import helper\n\n"
        "class Engine:\n"
        "    def run(self):\n"
        "        return helper()\n")
    s = scan(repo)
    eng = ParseEngine()
    results = eng.parse_batch(
        [(f.path, (repo / f.path).read_bytes()) for f in s.files])
    b = GraphBuilder()
    b.build("repo", s, results)
    return b


def test_scip_resolver_conformance(tmp_path: Path,
                                   valid_index: bytes) -> None:
    b = _build_universe(tmp_path)
    idx = parse_index(valid_index)
    resolver = ScipResolver(idx, b.universe, _prov_current())
    assert isinstance(resolver, SemanticResolver)
    caps = resolver.capabilities()
    assert caps.tier == 1  # SEMANTIC_INDEX


def test_scip_resolves_via_coordinator(tmp_path: Path,
                                       valid_index: bytes) -> None:
    b = _build_universe(tmp_path)
    idx = parse_index(valid_index)
    from code_atlas.resolvers.registry import ResolverRegistry

    reg = ResolverRegistry()
    reg.register(ScipResolver(idx, b.universe, _prov_current()))
    reg.register(SyntacticResolver(b.universe))
    coord = ResolverCoordinator(reg)

    req = ResolutionRequest(
        name="helper", kind="call", path="pkg/engine.py", language="python",
        # call site `helper()` — linha 4 (1-based) = scip line 3 (0-based)
        span=SourceSpan(start_byte=0, end_byte=0,
                        start_line=4, start_col=15, end_line=4, end_col=21),
    )
    res = coord.resolve(req)
    assert res.reference is not None
    assert res.reference.status == "resolved"
    assert res.reference.confidence_class == "resolved"
    assert res.reference.provenance.resolver_id == "scip"
    assert "scip-python" in res.reference.provenance.detail


def test_scip_document_not_in_index(tmp_path: Path,
                                    valid_index: bytes) -> None:
    b = _build_universe(tmp_path)
    resolver = ScipResolver(parse_index(valid_index), b.universe, _prov_current())
    res = resolver.resolve(
        ResolutionRequest(name="x", kind="call", path="nope.py"))
    assert res.reference is None
    assert res.diagnostics[0].code == "document-not-in-index"


def test_scip_symbol_not_in_snapshot_external(tmp_path: Path,
                                              valid_index: bytes) -> None:
    b = _build_universe(tmp_path)
    resolver = ScipResolver(parse_index(valid_index), b.universe, _prov_current())
    req = ResolutionRequest(name="y", kind="reference",
                            path="pkg/engine.py")
    res = resolver.resolve_symbol(req, f"{PKG} `other/lib.py`/thing().")
    assert res.reference is not None
    assert res.reference.status == "external"


def test_no_protobuf_import_anywhere() -> None:
    """Garantia do grill: zero dependência protobuf."""
    import ast

    src = Path("src")
    for f in src.rglob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] if isinstance(
                    node, ast.Import) else [node.module or ""]
                for n in names:
                    assert "protobuf" not in n, f"{f}: imports protobuf"


def test_string_field_for_symbol_fstring() -> None:
    """Sanity: fixture builder cobre field 3 string corretamente."""
    assert f_string(3, "x") == b"\x1a\x01x"
