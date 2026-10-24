"""Ports hexagonais do domínio (Blueprint §3).

Interfaces ``Protocol`` — apenas assinaturas. Implementações reais do
Cycle 1: ``RepositoryScanner`` (discovery.scan), ``GraphStore`` parcial
(storage), ``IndexCoordinator`` (indexing). As demais são contrato para
ciclos futuros e não têm implementação placebo.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from code_atlas.contracts.enums import ResolutionStatus
from code_atlas.contracts.models import Snapshot, SymbolOccurrence
from code_atlas.discovery.models import ScanOptions, ScanResult


class RepositoryScanner(Protocol):
    """Produz o inventário de arquivos de uma raiz (implementado: discovery)."""

    def scan(
        self, root: Path, options: ScanOptions | None = None
    ) -> ScanResult: ...


class ParserAdapter(Protocol):
    """Adaptador AST (Tree-sitter, Cycle 2) com capability matrix declarada."""

    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    def languages(self) -> frozenset[str]: ...

    def capabilities(self) -> frozenset[str]:
        """Ex.: ``symbols``, ``imports``, ``calls``, ``docstrings``."""
        ...


class SymbolResolver(Protocol):
    """Resolve ocorrências para alvos canônicos (SCIP/LSP, Cycle 3)."""

    @property
    def name(self) -> str: ...

    def resolve(self, occurrence: SymbolOccurrence) -> ResolutionStatus: ...


class GraphStore(Protocol):
    """Persistência do grafo tipado (implementado parcialmente: storage)."""

    def latest_ready_snapshot(self, repository_id: int) -> Snapshot | None: ...

    def snapshot_by_id(self, snapshot_id: int) -> Snapshot | None: ...


class SearchPort(Protocol):
    """Busca lexical/estrutural (FTS5, Cycle 4)."""

    def search(self, query: str, limit: int = 20) -> Sequence[object]: ...


class IndexCoordinator(Protocol):
    """Orquestra scan → snapshot → publicação atômica (implementado: indexing)."""

    def index(self, root: Path) -> Snapshot: ...
