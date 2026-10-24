"""Subsistema de busca (Cycle 2 Phase 6 + Cycle 4 lf-0036)."""

from code_atlas.search.contracts import (
    SearchCapability,
    SearchDiagnostic,
    SearchHit,
    SearchRequest,
    SearchResult,
)
from code_atlas.search.engine import SearchEngine
from code_atlas.search.structural import SearchFilters, StructuralSearch

__all__ = [
    "SearchCapability",
    "SearchDiagnostic",
    "SearchEngine",
    "SearchFilters",
    "SearchHit",
    "SearchRequest",
    "SearchResult",
    "StructuralSearch",
]
