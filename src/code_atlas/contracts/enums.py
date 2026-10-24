"""Vocabulário canônico do domínio (Blueprint §5).

Valores devem casar exatamente com o Blueprint — não renomear nem estender
sem ADR. Valores futuros documentados (READS_FROM etc.) ficam de fora até
existir produtor real.
"""

from __future__ import annotations

from enum import StrEnum


class NodeKind(StrEnum):
    """Tipos de nó do grafo (Blueprint §5 "Tipos iniciais")."""

    REPOSITORY = "repository"
    PACKAGE = "package"
    MODULE = "module"
    FILE = "file"
    CLASS = "class"
    INTERFACE = "interface"
    FUNCTION = "function"
    METHOD = "method"
    SYMBOL = "symbol"
    CONFIG = "config"
    UNKNOWN = "unknown"


class EdgeKind(StrEnum):
    """Vocabulário de arestas (Blueprint §5)."""

    CONTAINS = "CONTAINS"
    DEFINES = "DEFINES"
    IMPORTS = "IMPORTS"
    CALLS = "CALLS"
    IMPLEMENTS = "IMPLEMENTS"
    EXTENDS = "EXTENDS"
    REFERENCES = "REFERENCES"
    CONFIGURES = "CONFIGURES"
    TESTS = "TESTS"


class ConfidenceClass(StrEnum):
    """Classe de confiança da evidência — não é probabilidade (Blueprint §5)."""

    RESOLVED = "resolved"
    SYNTACTIC = "syntactic"
    HEURISTIC = "heuristic"
    INFERRED = "inferred"


class ResolutionStatus(StrEnum):
    """Status de resolução de um alvo de relação (Blueprint §5)."""

    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"
    EXTERNAL = "external"


class SnapshotStatus(StrEnum):
    """Estados do ciclo de vida de um snapshot (Blueprint §5)."""

    BUILDING = "BUILDING"
    READY = "READY"
    FAILED = "FAILED"
    STALE = "STALE"
