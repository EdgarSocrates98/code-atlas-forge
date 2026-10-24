"""Subsistema de parsing por adaptadores (Cycle 2 — Phases 1+2).

Boundary: apenas ``parsers/`` pode importar ``tree_sitter``. Código de
domínio (contracts, discovery, storage, graph) consome os contratos
tipados daqui sem conhecer a implementação concreta.
"""

from code_atlas.parsers.contracts import (
    EvidenceRecord,
    ExtractedReference,
    ExtractedRelation,
    ExtractedSymbol,
    GraphBuildResult,
    GraphEdge,
    GraphNode,
    ParseDiagnostic,
    ParserAdapter,
    ParserCapability,
    ParseRequest,
    ParseResult,
    SourceSpan,
)
from code_atlas.parsers.engine import ParseEngine, ParseLimits
from code_atlas.parsers.registry import ParserRegistry

__all__ = [
    "EvidenceRecord",
    "ExtractedReference",
    "ExtractedRelation",
    "ExtractedSymbol",
    "GraphBuildResult",
    "GraphEdge",
    "GraphNode",
    "ParseDiagnostic",
    "ParseEngine",
    "ParseLimits",
    "ParseRequest",
    "ParseResult",
    "ParserAdapter",
    "ParserCapability",
    "ParserRegistry",
    "SourceSpan",
]
