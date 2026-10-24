"""Contratos canônicos de evidência + validador (lf-0051).

Toda referência carrega identidade suficiente para resolver sem
ambiguidade: repository_id + snapshot_id + ids de domínio. O
``EvidenceValidator`` revalida contra SQLite — cross-repo/cross-
snapshot/spans inválidos falham com ``EvidenceValidationResult``
tipado, nunca silencioso.

Kinds de evidência diferenciados:
- ``lexical-match``     — casou via FTS/bm25 (matched_fields)
- ``syntactic-occurrence`` — ocorrência sintática persistida
- ``graph-relation``    — aresta do grafo (relation_id)
- ``resolver-resolution`` — resolução externa (SCIP/LSP)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

EVIDENCE_KINDS = frozenset({
    "lexical-match", "syntactic-occurrence", "graph-relation",
    "resolver-resolution",
})


@dataclass(frozen=True)
class SnapshotRef:
    repository_id: int
    snapshot_id: int


@dataclass(frozen=True)
class SymbolRef:
    repository_id: int
    symbol_id: int
    qualified_name: str
    kind: str
    language: str


@dataclass(frozen=True)
class OccurrenceRef:
    repository_id: int
    snapshot_id: int
    occurrence_id: int
    symbol_id: int
    qualified_name: str
    source_path: str
    span_start: int | None
    span_end: int | None
    content_hash: str | None
    parse_status: str


@dataclass(frozen=True)
class RelationRef:
    repository_id: int
    snapshot_id: int
    relation_id: int
    type: str
    from_occurrence: int
    to_occurrence: int | None
    to_unresolved: str | None
    resolution_status: str
    confidence_class: str
    resolver: str | None


@dataclass(frozen=True)
class SourceFileRef:
    repository_id: int
    snapshot_id: int
    file_id: int
    relative_path: str
    content_hash: str | None


@dataclass(frozen=True)
class EvidenceRef:
    """Uma evidência tipada apontando para um ref canônico."""

    kind: str                       # EVIDENCE_KINDS
    ref: OccurrenceRef | RelationRef | SourceFileRef | SymbolRef
    origin_strategy: str            # lexical|structural|graph|vector|hybrid
    detail: str = ""


@dataclass(frozen=True)
class EvidenceValidationResult:
    ok: bool
    code: str        # "" | not-found | cross-repo | cross-snapshot | bad-span
    message: str


_OK = EvidenceValidationResult(True, "", "")


class EvidenceValidator:
    """Revalida refs contra o banco — sem fabricar evidência."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        conn.row_factory = sqlite3.Row

    def _repo_of_snapshot(self, snapshot_id: int) -> int | None:
        row = self._conn.execute(
            "SELECT repository_id FROM snapshots WHERE id = ?",
            (snapshot_id,)).fetchone()
        return int(row["repository_id"]) if row else None

    def validate_occurrence(
        self, ref: OccurrenceRef
    ) -> EvidenceValidationResult:
        repo = self._repo_of_snapshot(ref.snapshot_id)
        if repo is None:
            return EvidenceValidationResult(
                False, "not-found", "snapshot inexistente")
        if repo != ref.repository_id:
            return EvidenceValidationResult(
                False, "cross-repo",
                f"occurrence em repo {repo}, ref declara "
                f"{ref.repository_id}")
        row = self._conn.execute(
            "SELECT o.id, o.span_start, o.span_end, o.parse_status,"
            " f.relative_path FROM symbol_occurrences o"
            " JOIN source_files f ON f.id = o.source_file_id"
            " WHERE o.id = ? AND o.snapshot_id = ?",
            (ref.occurrence_id, ref.snapshot_id)).fetchone()
        if row is None:
            return EvidenceValidationResult(
                False, "not-found",
                f"occurrence {ref.occurrence_id} ausente no snapshot")
        if row["relative_path"] != ref.source_path:
            return EvidenceValidationResult(
                False, "cross-snapshot",
                "occurrence existe mas em outro arquivo")
        if (ref.span_start is not None
                and row["span_start"] != ref.span_start):
            return EvidenceValidationResult(
                False, "bad-span", "span diverge do persistido")
        return _OK

    def validate_relation(
        self, ref: RelationRef
    ) -> EvidenceValidationResult:
        repo = self._repo_of_snapshot(ref.snapshot_id)
        if repo is None or repo != ref.repository_id:
            return EvidenceValidationResult(
                False, "cross-repo", "relação fora do repo/snapshot")
        row = self._conn.execute(
            "SELECT id, from_id, to_id, type, resolution_status"
            " FROM relations WHERE id = ? AND snapshot_id = ?",
            (ref.relation_id, ref.snapshot_id)).fetchone()
        if row is None:
            return EvidenceValidationResult(
                False, "not-found",
                f"relation {ref.relation_id} ausente")
        if row["from_id"] != ref.from_occurrence:
            return EvidenceValidationResult(
                False, "bad-span", "origem da relação diverge")
        return _OK


class EvidenceGrounder:
    """Materializa ``EvidenceRef``s tipadas a partir de um SearchHit —
    rastreabilidade hit → ocorrência → origem (lf-0051)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        conn.row_factory = sqlite3.Row

    def occurrence_ref(
        self, repository_id: int, snapshot_id: int, occurrence_id: int,
    ) -> OccurrenceRef | None:
        row = self._conn.execute(
            "SELECT o.id, o.logical_symbol_id, o.span_start, o.span_end,"
            " o.content_hash, o.parse_status, f.relative_path,"
            " s.qualified_name FROM symbol_occurrences o"
            " JOIN source_files f ON f.id = o.source_file_id"
            " JOIN symbols s ON s.id = o.logical_symbol_id"
            " WHERE o.id = ? AND o.snapshot_id = ?",
            (occurrence_id, snapshot_id)).fetchone()
        if row is None:
            return None
        return OccurrenceRef(
            repository_id=repository_id, snapshot_id=snapshot_id,
            occurrence_id=occurrence_id,
            symbol_id=int(row["logical_symbol_id"]),
            qualified_name=str(row["qualified_name"]),
            source_path=str(row["relative_path"]),
            span_start=row["span_start"], span_end=row["span_end"],
            content_hash=row["content_hash"],
            parse_status=str(row["parse_status"]))

    def ground_hit(
        self,
        repository_id: int,
        snapshot_id: int,
        hit: object,
    ) -> tuple[EvidenceRef, ...]:
        """Refs tipadas para um hit: ocorrência + relations + resolver.

        Sem evidência persistida → tupla vazia (ausência identificada,
        nunca fabricada)."""
        occ_id = int(getattr(hit, "occurrence_id", 0) or 0)
        strategy = str(getattr(hit, "strategy", ""))
        out: list[EvidenceRef] = []
        occ = self.occurrence_ref(repository_id, snapshot_id, occ_id)
        if occ is not None:
            kind = ("lexical-match" if strategy == "lexical"
                    else "syntactic-occurrence")
            out.append(EvidenceRef(kind=kind, ref=occ,
                                   origin_strategy=strategy))
        rows = self._conn.execute(
            "SELECT id, from_id, to_id, to_unresolved, type,"
            " resolution_status, confidence_class, resolver"
            " FROM relations WHERE snapshot_id = ? AND ("
            " from_id = ? OR to_id = ?) ORDER BY id LIMIT 50",
            (snapshot_id, occ_id, occ_id)).fetchall()
        for r in rows:
            out.append(EvidenceRef(
                kind="graph-relation",
                ref=RelationRef(
                    repository_id=repository_id,
                    snapshot_id=snapshot_id,
                    relation_id=int(r["id"]), type=str(r["type"]),
                    from_occurrence=int(r["from_id"]),
                    to_occurrence=r["to_id"],
                    to_unresolved=r["to_unresolved"],
                    resolution_status=str(r["resolution_status"]),
                    confidence_class=str(r["confidence_class"]),
                    resolver=r["resolver"]),
                origin_strategy="graph" if strategy == "graph"
                else strategy,
                detail="outgoing" if int(r["from_id"]) == occ_id
                else "incoming"))
        return tuple(out)
