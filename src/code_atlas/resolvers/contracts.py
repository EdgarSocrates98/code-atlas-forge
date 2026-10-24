"""Contratos versionados do subsistema de resolução (lf-0016).

Ordem conceitual de evidência (prompt Cycle 3 §Phase 1):

1. Índice semântico confiável, consistente com a revisão (SCIP).
2. LSP com definição/referência confirmada.
3. Resolução sintática inequívoca.
4. Candidato heurístico.
5. Alvo ambíguo ou não resolvido.

``confidence_class`` e ``resolution_status`` permanecem conceitos
distintos: o primeiro descreve a força da evidência do resolvedor, o
segundo o desfecho da tentativa (resolved/ambiguous/unresolved/external).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from code_atlas.parsers.contracts import SourceSpan
from code_atlas.resolvers.capabilities import ResolverCapability

RESOLUTION_SCHEMA_VERSION = 1

# Buckets de kind aceitos pelo contrato — adapters SCIP/LSP/syntactic
# mapeiam seus pedidos para este vocabulário.
REQUEST_KINDS = frozenset(
    {"call", "import", "type-ref", "reference", "definition"}
)


@dataclass(frozen=True)
class ResolverProvenance:
    """Origem verificável de um resultado de resolução."""

    resolver_id: str          # ex.: "syntactic", "scip", "lsp:pyright"
    resolver_version: str     # versão do resolvedor (semver interno)
    evidence_tier: int        # EvidenceTier — 1=semantic-index…4=heuristic
    detail: str = ""          # indexer/server de origem, grammar, etc.


@dataclass(frozen=True)
class ResolutionRequest:
    """Pedido de resolução de um símbolo/referência textual."""

    name: str                  # texto a resolver (callee, nome, import)
    kind: str                  # call | import | type-ref | reference | definition
    path: str                  # arquivo de origem (relativo ao repo)
    language: str = ""
    span: SourceSpan | None = None
    module: str = ""           # qname do módulo de origem, se conhecido


@dataclass(frozen=True)
class ResolvedReference:
    """Resultado de uma resolução de referência/call/import."""

    status: str                       # resolved|ambiguous|unresolved|external
    confidence_class: str             # resolved|syntactic|heuristic|inferred
    provenance: ResolverProvenance
    target_id: str | None = None      # node_id interno quando resolvido
    target_text: str = ""             # texto preservado do alvo
    candidates: tuple[str, ...] = ()  # alvos concorrentes quando ambiguous


@dataclass(frozen=True)
class ResolvedDefinition:
    """Definição localizada de um símbolo (definition site)."""

    symbol: str
    path: str
    provenance: ResolverProvenance
    span: SourceSpan | None = None
    kind: str = ""


@dataclass(frozen=True)
class ResolutionDiagnostic:
    """Diagnóstico de um resolvedor (falha, timeout, rejeição)."""

    code: str
    severity: str  # info | warning | error
    message: str
    resolver_id: str = ""


@dataclass(frozen=True)
class ResolutionResult:
    """Envelope versionado do resultado de ``SemanticResolver.resolve``."""

    request: ResolutionRequest
    schema_version: int = RESOLUTION_SCHEMA_VERSION
    reference: ResolvedReference | None = None
    definitions: tuple[ResolvedDefinition, ...] = ()
    diagnostics: tuple[ResolutionDiagnostic, ...] = field(default_factory=tuple)


@runtime_checkable
class SemanticResolver(Protocol):
    """Interface implementada por qualquer fonte de resolução."""

    @property
    def resolver_id(self) -> str: ...

    @property
    def resolver_version(self) -> str: ...

    def capabilities(self) -> ResolverCapability:
        """O que este resolvedor cobre (linguagens, relações, tier)."""
        ...

    def resolve(self, request: ResolutionRequest) -> ResolutionResult:
        """Tenta resolver o pedido; nunca levanta — falhas viram
        ``ResolutionDiagnostic`` no resultado."""
        ...
