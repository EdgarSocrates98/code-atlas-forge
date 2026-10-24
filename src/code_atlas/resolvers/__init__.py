"""Subsistema de resolução semântica (Cycle 3 Phase 1).

Independente de parsers, storage e CLI: cada ``SemanticResolver`` declara
capabilities e proveniência; o ``ResolverCoordinator`` encadeia por ordem
de evidência (índice semântico > LSP > sintático > heurístico).
"""

from code_atlas.resolvers.capabilities import EvidenceTier, ResolverCapability
from code_atlas.resolvers.contracts import (
    ResolutionDiagnostic,
    ResolutionRequest,
    ResolutionResult,
    ResolvedDefinition,
    ResolvedReference,
    ResolverProvenance,
    SemanticResolver,
)
from code_atlas.resolvers.coordinator import ResolverCoordinator
from code_atlas.resolvers.registry import ResolverRegistry

__all__ = [
    "EvidenceTier",
    "ResolutionDiagnostic",
    "ResolutionRequest",
    "ResolutionResult",
    "ResolvedDefinition",
    "ResolvedReference",
    "ResolverCapability",
    "ResolverCoordinator",
    "ResolverProvenance",
    "ResolverRegistry",
    "SemanticResolver",
]
