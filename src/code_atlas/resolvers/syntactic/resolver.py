"""Resolvedor sintático (EvidenceTier.SYNTACTIC) — lf-0016.

Encapsula a resolução de escopo do Cycle 2 — a mesma lógica que vivia em
``graph.builder`` agora atrás do contrato ``SemanticResolver``. O universo
(``ScopeUniverse``) é montado pelo GraphBuilder durante a construção e
fornecido ao resolver para a fase de resolução.

Regras inalteradas (regressão garantida pelos testes do Cycle 2):

- ``resolved`` só com alvo único no snapshot — nunca por coincidência.
- ``obj.attr`` sem tipo conhecido de ``obj`` → ``unresolved``.
- Alias de import resolve via ``import_targets`` do módulo de origem.
- Imports sem alvo local distinguem ``external`` (stdlib/site-packages)
  de ``unresolved`` (relativo/pacote local quebrado).
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field

from code_atlas.resolvers.capabilities import EvidenceTier, ResolverCapability
from code_atlas.resolvers.contracts import (
    ResolutionDiagnostic,
    ResolutionRequest,
    ResolutionResult,
    ResolvedReference,
    ResolverProvenance,
)

RESOLVER_ID = "syntactic"
RESOLVER_VERSION = "1.0.0"


@dataclass
class ModuleCtx:
    """Contexto de resolução de um módulo parseado."""

    path: str
    module_name: str
    language: str
    nid: str
    defines: dict[str, list[str]] = field(default_factory=dict)
    import_targets: dict[str, str] = field(default_factory=dict)


@dataclass
class ScopeUniverse:
    """Tabelas de escopo montadas pelo builder para a fase de resolução."""

    modules: dict[str, ModuleCtx] = field(default_factory=dict)
    by_path: dict[str, str] = field(default_factory=dict)
    nid_mod: dict[str, ModuleCtx] = field(default_factory=dict)
    top_packages: set[str] = field(default_factory=set)
    leaf: dict[str, list[str]] = field(default_factory=dict)
    qname_nid: dict[str, list[str]] = field(default_factory=dict)

    def ctx_for_path(self, path: str) -> ModuleCtx | None:
        nid = self.by_path.get(path)
        return self.nid_mod.get(nid) if nid else None


class SyntacticResolver:
    """Implementa ``SemanticResolver`` sobre o ``ScopeUniverse``."""

    def __init__(self, universe: ScopeUniverse | None = None) -> None:
        self._universe = universe or ScopeUniverse()

    @property
    def resolver_id(self) -> str:
        return RESOLVER_ID

    @property
    def resolver_version(self) -> str:
        return RESOLVER_VERSION

    @property
    def universe(self) -> ScopeUniverse:
        return self._universe

    def capabilities(self) -> ResolverCapability:
        return ResolverCapability(
            resolver_id=RESOLVER_ID,
            tier=EvidenceTier.SYNTACTIC,
            languages=frozenset(),      # qualquer linguagem parseada
            relation_types=frozenset(
                {"IMPORTS", "CALLS", "REFERENCES", "EXTENDS", "IMPLEMENTS"}
            ),
            detail="local scope resolution (no type info)",
        )

    def _prov(self) -> ResolverProvenance:
        return ResolverProvenance(
            resolver_id=RESOLVER_ID,
            resolver_version=RESOLVER_VERSION,
            evidence_tier=int(EvidenceTier.SYNTACTIC),
        )

    # -- contrato SemanticResolver --------------------------------------

    def resolve(self, request: ResolutionRequest) -> ResolutionResult:
        ctx = self._universe.ctx_for_path(request.path)
        if ctx is None:
            return ResolutionResult(
                request=request,
                diagnostics=(
                    ResolutionDiagnostic(
                        code="module-not-indexed",
                        severity="warning",
                        message=f"no module context for {request.path}",
                        resolver_id=RESOLVER_ID,
                    ),
                ),
            )
        if request.kind == "import":
            nid, status = self.resolve_import(ctx, request.name)
        else:
            nid, status = self.resolve_name(ctx, request.name)
        conf = "resolved" if status == "resolved" else "syntactic"
        return ResolutionResult(
            request=request,
            reference=ResolvedReference(
                status=status,
                confidence_class=conf,
                provenance=self._prov(),
                target_id=nid,
                target_text=request.name,
            ),
        )

    # -- primitivas de resolução (extraídas de graph.builder) ------------

    def scope_nid(
        self, ctx: ModuleCtx, scope_path: tuple[str, ...]
    ) -> str | None:
        qname = ".".join((ctx.module_name, *scope_path))
        nids = self._universe.qname_nid.get(qname)
        return nids[0] if nids else ctx.nid

    def by_qname_or_module(self, ctx: ModuleCtx, qname: str) -> str | None:
        nids = self._universe.qname_nid.get(qname)
        if nids:
            return nids[0]
        # adapters podem reportar from_name sem prefixo de módulo
        nids = self._universe.qname_nid.get(f"{ctx.module_name}.{qname}")
        if nids:
            return nids[0]
        return ctx.nid  # fallback: origem = módulo

    def resolve_import(
        self, ctx: ModuleCtx, target: str
    ) -> tuple[str | None, str]:
        """IMPORTS: módulo local → resolved; relativo/path falho →
        unresolved; stdlib/site-packages → external."""
        mod, _rest = self.locate(ctx, target)
        if mod is not None:
            return mod.nid, "resolved"
        if target.startswith(".") or (
            target.rsplit(".", 1)[0] in self._universe.top_packages
        ):
            return None, "unresolved"
        return None, "external"

    def locate(
        self, ctx: ModuleCtx, target: str
    ) -> tuple[ModuleCtx | None, list[str]]:
        """Alvo dotted/pathed → (ctx do módulo, segmentos-membro restantes).

        Cobre: python absoluto (``pkg.util``), python relativo (``.util``),
        TS relativo (``./util``, ``../x``).
        """
        if target.startswith(("./", "../")):
            base, _, member = target.rpartition(".")
            nid = self.resolve_ts_relative(ctx, base)
            rest = [member] if member and member != "*" else []
            if nid is None:  # último segmento era do path, não member
                nid = self.resolve_ts_relative(ctx, target)
                rest = []
            mod = self._universe.nid_mod.get(nid) if nid else None
            return mod, rest
        cand = target
        if target.startswith("."):  # python relativo
            dots = len(target) - len(target.lstrip("."))
            rest_txt = target.lstrip(".")
            parts = ctx.module_name.split(".")
            # lf-0130: `pkg/__init__.py` É o pacote `pkg` — `.` já resolve
            # para o módulo inteiro, não para o pai dele (`from .api
            # import x` no __init__ → ``pkg.api.x``, não ``api.x``).
            if posixpath.basename(ctx.path) == "__init__.py":
                dots -= 1
            pkg_parts = parts[: max(len(parts) - dots, 0)]
            cand = ".".join([*pkg_parts, rest_txt]) if rest_txt else ".".join(
                pkg_parts
            )
        segs = cand.split(".")
        for i in range(len(segs), 0, -1):
            mod = self._universe.modules.get(".".join(segs[:i]))
            if mod is not None:
                return mod, segs[i:]
        return None, segs

    def resolve_ts_relative(self, ctx: ModuleCtx, target: str) -> str | None:
        """TS: ``./util`` → path relativo ao diretório do arquivo."""
        if not target.startswith(("./", "../")):
            return None
        base_dir = posixpath.dirname(ctx.path)
        norm = posixpath.normpath(posixpath.join(base_dir, target))
        # lf-0130: .d.mts/.d.cts e index.d.ts cobertos — mesma família de
        # extensões que o adapter TypeScript registra.
        for ext in (".ts", ".tsx", ".d.ts", ".mts", ".cts",
                    ".d.mts", ".d.cts",
                    "/index.ts", "/index.tsx", "/index.d.ts"):
            nid = self._universe.by_path.get(norm + ext)
            if nid:
                return nid
        return self._universe.by_path.get(norm)

    def resolve_name(
        self, ctx: ModuleCtx, name: str
    ) -> tuple[str | None, str]:
        """Nome bare/dotted → nó local único, senão status defensável."""
        head, _, tail = name.partition(".")
        if not tail and name in ctx.defines and name in ctx.import_targets:
            # def local + binding de import competem pelo mesmo nome —
            # sem ordem textual o desfecho honesto é ambiguous, nunca
            # um "resolved" para o alvo plausível-mas-errado (lf-0024).
            return None, "ambiguous"
        if head in ctx.import_targets:
            mod, rest = self.locate(ctx, ctx.import_targets[head])
            if mod is None:
                return None, "external"
            chain = [s for s in (*rest, tail) if s and s != "*"]
            if not chain:
                return mod.nid, "resolved"
            hits = sorted(set(mod.defines.get(chain[0], [])))
            if len(hits) == 1:
                return hits[0], "resolved"
            if hits:
                return None, "ambiguous"
            return None, "unresolved"
        if tail:
            # `obj.attr` sem tipo de obj — nunca resolve por coincidência
            return None, "unresolved"
        local = sorted(set(ctx.defines.get(name, [])))
        if len(local) == 1:
            return local[0], "resolved"
        if len(local) > 1:
            return None, "ambiguous"
        return None, "unresolved"

    def resolve_call(
        self, ctx: ModuleCtx, callee: str
    ) -> tuple[str | None, str]:
        return self.resolve_name(ctx, callee)
