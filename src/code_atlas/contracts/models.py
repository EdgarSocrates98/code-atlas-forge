"""Modelos de domínio do Code Atlas (Blueprint §5).

Dataclasses stdlib, imutáveis onde possível. Campos de enum aceitam o valor
string ou o enum e são validados em ``__post_init__`` — valores fora do
vocabulário levantam ``ValueError``. ``id=None`` significa "a persistir".

Identidade lógica (``LogicalSymbol``, ``SourceFile``) é separada da
ocorrência por snapshot (``SymbolOccurrence``, ``file_inventory``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from code_atlas.contracts.enums import (
    ConfidenceClass,
    EdgeKind,
    NodeKind,
    ResolutionStatus,
    SnapshotStatus,
)


def _coerce(enum_cls: type, value: object, field_name: str) -> object:
    try:
        return enum_cls(value)
    except ValueError as exc:
        raise ValueError(f"invalid {field_name}: {value!r}") from exc


@dataclass(frozen=True)
class Repository:
    """Repositório indexado; `id` independe do pathname local."""

    root_handle: str
    id: int | None = None
    origin: str = ""
    git_provider: str = ""
    default_branch: str = ""


@dataclass(frozen=True)
class Snapshot:
    """Ocorrência imutável de um estado do repositório."""

    repository_id: int
    worktree_digest: str
    id: int | None = None
    git_ref: str = ""
    commit_sha: str | None = None
    dirty: bool = False  # lf-0021: conteúdo diverge do commit_sha
    analyzer_versions: Mapping[str, str] = field(default_factory=dict)
    parent_snapshot_id: int | None = None
    status: SnapshotStatus = SnapshotStatus.BUILDING
    created_at: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "status", _coerce(SnapshotStatus, self.status, "status")
        )


@dataclass(frozen=True)
class LogicalSymbol:
    """Símbolo com identidade lógica estável por repositório."""

    repository_id: int
    language: str
    qualified_name: str
    kind: NodeKind
    id: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _coerce(NodeKind, self.kind, "kind"))


@dataclass(frozen=True)
class DomainSourceFile:
    """Arquivo-fonte com identidade por (repo, relative_path)."""

    repository_id: int
    relative_path: str
    language: str
    id: int | None = None
    sha256: str | None = None
    size_bytes: int = 0
    ignored: bool = False


@dataclass(frozen=True)
class SymbolOccurrence:
    """Ocorrência de um símbolo num snapshot (span/hash são da ocorrência)."""

    snapshot_id: int
    logical_symbol_id: int
    source_file_id: int
    id: int | None = None
    span_start: int | None = None
    span_end: int | None = None
    line: int | None = None
    col: int | None = None
    content_hash: str | None = None
    parse_status: str = "parsed"


@dataclass(frozen=True)
class Relation:
    """Aresta tipada com proveniência obrigatória (Blueprint ADR-012)."""

    snapshot_id: int
    from_id: int
    relation_type: EdgeKind
    resolver: str
    confidence_class: ConfidenceClass
    resolution_status: ResolutionStatus
    id: int | None = None
    to_id: int | None = None
    to_unresolved: str | None = None
    evidence_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "relation_type", _coerce(EdgeKind, self.relation_type, "relation_type")
        )
        object.__setattr__(
            self,
            "confidence_class",
            _coerce(ConfidenceClass, self.confidence_class, "confidence_class"),
        )
        object.__setattr__(
            self,
            "resolution_status",
            _coerce(ResolutionStatus, self.resolution_status, "resolution_status"),
        )
        if self.to_id is None and not self.to_unresolved:
            raise ValueError("relation requires to_id or to_unresolved target")
        if not self.resolver:
            raise ValueError("relation requires resolver provenance")


@dataclass(frozen=True)
class Evidence:
    """Trecho observável que sustenta um fato (extrator + versão + hash)."""

    snapshot_id: int
    source_file_id: int
    path: str
    content_hash: str
    extractor: str
    extractor_version: str
    id: int | None = None
    span: str | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.content_hash:
            raise ValueError("evidence requires content_hash")
        if not self.extractor or not self.extractor_version:
            raise ValueError("evidence requires extractor + extractor_version")


@dataclass(frozen=True)
class IndexJob:
    """Registro de uma tentativa de indexação (diagnóstico)."""

    repository_id: int
    status: str  # running | success | failed | noop
    id: int | None = None
    snapshot_id: int | None = None
    started_at: str = ""
    finished_at: str = ""
    counts: Mapping[str, int] = field(default_factory=dict)
    failures: tuple[str, ...] = ()
    manifest_hash: str = ""
