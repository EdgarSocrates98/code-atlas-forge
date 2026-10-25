"""Subsistema de busca (Cycle 2 Phase 6 + Cycle 4 lf-0036).

lf-0119: a faceta de domínio (``DomainSearchHit``/``DomainEntitySearch``)
e a busca federada (``code_atlas.search.federated`` — importada pelas
superfícies, não reexportada aqui para não puxar ``code_atlas.federation``
em todo consumidor do engine).
"""

from code_atlas.search.contracts import (
    DomainSearchHit,
    SearchCapability,
    SearchDiagnostic,
    SearchHit,
    SearchRequest,
    SearchResult,
)
from code_atlas.search.domain import DomainEntitySearch
from code_atlas.search.engine import SearchEngine
from code_atlas.search.structural import SearchFilters, StructuralSearch

__all__ = [
    "DomainEntitySearch",
    "DomainSearchHit",
    "SearchCapability",
    "SearchDiagnostic",
    "SearchEngine",
    "SearchFilters",
    "SearchHit",
    "SearchRequest",
    "SearchResult",
    "StructuralSearch",
]
