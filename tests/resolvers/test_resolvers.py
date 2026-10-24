"""lf-0016: contratos de resolvers, cadeia de precedência e fronteira."""

from __future__ import annotations

import ast
from pathlib import Path

from code_atlas.resolvers import (
    EvidenceTier,
    ResolutionRequest,
    ResolutionResult,
    ResolvedReference,
    ResolverCapability,
    ResolverCoordinator,
    ResolverProvenance,
    ResolverRegistry,
    SemanticResolver,
)
from code_atlas.resolvers.syntactic import ScopeUniverse, SyntacticResolver
from code_atlas.resolvers.syntactic.resolver import ModuleCtx

SRC = Path(__file__).resolve().parents[2] / "src"


class _FakeResolver:
    """Resolver de teste configurável (tier/kind/status)."""

    def __init__(
        self, rid: str, tier: EvidenceTier, status: str,
        languages: frozenset[str] | None = None,
        relation_types: frozenset[str] | None = None,
        boom: bool = False,
    ) -> None:
        self._rid = rid
        self._cap = ResolverCapability(
            resolver_id=rid,
            tier=tier,
            languages=languages or frozenset(),
            relation_types=relation_types or frozenset(),
        )
        self._status = status
        self._boom = boom

    @property
    def resolver_id(self) -> str:
        return self._rid

    @property
    def resolver_version(self) -> str:
        return "0.0.0"

    def capabilities(self) -> ResolverCapability:
        return self._cap

    def resolve(self, request: ResolutionRequest) -> ResolutionResult:
        if self._boom:
            raise RuntimeError("resolver exploded")
        return ResolutionResult(
            request=request,
            reference=ResolvedReference(
                status=self._status,
                confidence_class="syntactic",
                provenance=ResolverProvenance(
                    resolver_id=self._rid,
                    resolver_version="0.0.0",
                    evidence_tier=int(self._cap.tier),
                ),
                target_id="nid:x" if self._status == "resolved" else None,
                target_text=request.name,
            ),
        )


def _req(kind: str = "call", language: str = "python") -> ResolutionRequest:
    return ResolutionRequest(name="helper", kind=kind, path="a.py",
                             language=language)


class TestContracts:
    def test_syntactic_conforms_to_protocol(self) -> None:
        r = SyntacticResolver()
        assert isinstance(r, SemanticResolver)
        cap = r.capabilities()
        assert cap.tier == EvidenceTier.SYNTACTIC

    def test_provenance_required_on_result(self) -> None:
        reg = ResolverRegistry()
        reg.register(_FakeResolver("scip", EvidenceTier.SEMANTIC_INDEX,
                                   "resolved"))
        out = ResolverCoordinator(reg).resolve(_req())
        ref = out.reference
        assert ref is not None
        prov = ref.provenance
        assert prov.resolver_id == "scip"
        assert prov.resolver_version
        assert prov.evidence_tier == int(EvidenceTier.SEMANTIC_INDEX)

    def test_schema_version_present(self) -> None:
        out = ResolutionResult(request=_req())
        assert out.schema_version >= 1


class TestCoordinatorPrecedence:
    def test_highest_tier_wins(self) -> None:
        reg = ResolverRegistry()
        reg.register(_FakeResolver("heur", EvidenceTier.HEURISTIC,
                                   "resolved"))
        reg.register(_FakeResolver("scip", EvidenceTier.SEMANTIC_INDEX,
                                   "resolved"))
        out = ResolverCoordinator(reg).resolve(_req())
        assert out.reference is not None
        assert out.reference.provenance.resolver_id == "scip"

    def test_falls_back_when_higher_tier_unresolved(self) -> None:
        reg = ResolverRegistry()
        reg.register(_FakeResolver("scip", EvidenceTier.SEMANTIC_INDEX,
                                   "unresolved"))
        reg.register(_FakeResolver("syn", EvidenceTier.SYNTACTIC,
                                   "resolved"))
        out = ResolverCoordinator(reg).resolve(_req())
        assert out.reference is not None
        assert out.reference.status == "resolved"
        assert out.reference.provenance.resolver_id == "syn"

    def test_never_promotes_unresolved_to_resolved(self) -> None:
        reg = ResolverRegistry()
        reg.register(_FakeResolver("scip", EvidenceTier.SEMANTIC_INDEX,
                                   "ambiguous"))
        out = ResolverCoordinator(reg).resolve(_req())
        assert out.reference is not None
        assert out.reference.status == "ambiguous"  # honesto, não promovido

    def test_resolver_exception_becomes_diagnostic(self) -> None:
        reg = ResolverRegistry()
        reg.register(_FakeResolver("bad", EvidenceTier.SEMANTIC_INDEX,
                                   "resolved", boom=True))
        reg.register(_FakeResolver("syn", EvidenceTier.SYNTACTIC,
                                   "resolved"))
        out = ResolverCoordinator(reg).resolve(_req())
        assert out.reference is not None
        assert out.reference.status == "resolved"
        assert any(d.code == "resolver-error" for d in out.diagnostics)

    def test_no_resolver_gives_diagnostic(self) -> None:
        out = ResolverCoordinator(ResolverRegistry()).resolve(_req())
        assert out.reference is None
        assert any(d.code == "no-resolver" for d in out.diagnostics)

    def test_language_gating(self) -> None:
        reg = ResolverRegistry()
        reg.register(_FakeResolver(
            "ts-only", EvidenceTier.SYNTACTIC, "resolved",
            languages=frozenset({"typescript"})))
        out = ResolverCoordinator(reg).resolve(_req(language="python"))
        assert out.reference is None  # ts-only não cobre python


class TestSyntacticResolver:
    def _universe(self) -> ScopeUniverse:
        u = ScopeUniverse()
        ctx_a = ModuleCtx(path="a.py", module_name="a", language="python",
                          nid="module:a")
        ctx_a.defines["f"] = ["sym:a.f"]
        ctx_a.import_targets["helper"] = "b.helper"
        ctx_b = ModuleCtx(path="b.py", module_name="b", language="python",
                          nid="module:b")
        ctx_b.defines["helper"] = ["sym:b.helper"]
        u.modules["a"] = ctx_a
        u.modules["b"] = ctx_b
        u.by_path["a.py"] = "module:a"
        u.by_path["b.py"] = "module:b"
        u.nid_mod["module:a"] = ctx_a
        u.nid_mod["module:b"] = ctx_b
        u.qname_nid["a.f"] = ["sym:a.f"]
        u.qname_nid["b.helper"] = ["sym:b.helper"]
        return u

    def test_resolve_local_name(self) -> None:
        r = SyntacticResolver(self._universe())
        out = r.resolve(ResolutionRequest(
            name="f", kind="call", path="a.py", language="python"))
        assert out.reference is not None
        assert out.reference.status == "resolved"
        assert out.reference.target_id == "sym:a.f"
        assert out.reference.provenance.resolver_id == "syntactic"
        assert out.reference.provenance.evidence_tier == int(
            EvidenceTier.SYNTACTIC)

    def test_resolve_via_import_alias(self) -> None:
        r = SyntacticResolver(self._universe())
        out = r.resolve(ResolutionRequest(
            name="helper", kind="call", path="a.py", language="python"))
        assert out.reference is not None
        assert out.reference.status == "resolved"
        assert out.reference.target_id == "sym:b.helper"

    def test_unindexed_path_diagnostic(self) -> None:
        r = SyntacticResolver(self._universe())
        out = r.resolve(ResolutionRequest(
            name="f", kind="call", path="missing.py", language="python"))
        assert out.reference is None
        assert any(d.code == "module-not-indexed" for d in out.diagnostics)

    def test_ambiguous_local_names(self) -> None:
        u = self._universe()
        u.modules["a"].defines["dup"] = ["sym:a.dup1", "sym:a.dup2"]
        r = SyntacticResolver(u)
        out = r.resolve(ResolutionRequest(
            name="dup", kind="call", path="a.py", language="python"))
        assert out.reference is not None
        assert out.reference.status == "ambiguous"


class TestArchitecturalBoundary:
    """resolvers/ não pode depender de storage/cli/discovery internals."""

    FORBIDDEN = ("code_atlas.storage", "code_atlas.cli",
                 "code_atlas.discovery", "code_atlas.indexing")

    def test_no_forbidden_imports(self) -> None:
        bad: list[str] = []
        pkg = SRC / "code_atlas" / "resolvers"
        for py in pkg.rglob("*.py"):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                mod = ""
                if isinstance(node, ast.Import):
                    mod = node.names[0].name
                elif isinstance(node, ast.ImportFrom) and node.module:
                    mod = node.module
                if mod and any(mod.startswith(f) for f in self.FORBIDDEN):
                    bad.append(f"{py.name}:{node.lineno}:{mod}")
        assert not bad, f"forbidden imports: {bad}"
