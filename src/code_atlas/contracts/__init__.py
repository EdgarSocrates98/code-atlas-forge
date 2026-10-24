"""Contratos de domínio do Code Atlas (modelos, enums, ports).

O domínio não importa CLI, web, MCP, UI ou bancos externos.
"""

from code_atlas.contracts.enums import (
    ConfidenceClass,
    EdgeKind,
    NodeKind,
    ResolutionStatus,
    SnapshotStatus,
)
from code_atlas.contracts.models import (
    DomainSourceFile,
    Evidence,
    IndexJob,
    LogicalSymbol,
    Relation,
    Repository,
    Snapshot,
    SymbolOccurrence,
)

__all__ = [
    "ConfidenceClass",
    "DomainSourceFile",
    "EdgeKind",
    "Evidence",
    "IndexJob",
    "LogicalSymbol",
    "NodeKind",
    "Relation",
    "Repository",
    "ResolutionStatus",
    "Snapshot",
    "SnapshotStatus",
    "SymbolOccurrence",
]
