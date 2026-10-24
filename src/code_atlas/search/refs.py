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
    code: str        # "" | not-found | cross-repo | cross-snapshot
                     # | bad-span | metadata-mismatch
    message: str


_OK = EvidenceValidationResult(True, "", "")


def _mismatch(field: str) -> EvidenceValidationResult:
    """Metadado declarado contradiz o persistido (lf-0058).

    ``None`` na ref = "não assertado" → não validado. Declarado e
    divergente → rejeitado, mesmo que o id exista."""
    return EvidenceValidationResult(
        False, "metadata-mismatch", f"campo '{field}' diverge")


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
            "SELECT o.id, o.logical_symbol_id, o.span_start, o.span_end,"
            " o.content_hash, o.parse_status, f.relative_path,"
            " s.qualified_name FROM symbol_occurrences o"
            " JOIN source_files f ON f.id = o.source_file_id"
            " JOIN symbols s ON s.id = o.logical_symbol_id"
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
        return self._check_occurrence_metadata(ref, row)

    def _check_occurrence_metadata(
        self, ref: OccurrenceRef, row: sqlite3.Row
    ) -> EvidenceValidationResult:
        """lf-0058: metadados declarados são revalidados — id existente
        não basta (adulteração de qname/símbolo/span/hash/status)."""
        if (ref.symbol_id
                and int(row["logical_symbol_id"]) != ref.symbol_id):
            return _mismatch("symbol_id")
        if (ref.qualified_name
                and str(row["qualified_name"]) != ref.qualified_name):
            return _mismatch("qualified_name")
        for col, span in (("span_start", ref.span_start),
                          ("span_end", ref.span_end)):
            if span is not None and row[col] != span:
                return EvidenceValidationResult(
                    False, "bad-span", f"{col} diverge do persistido")
        if (ref.content_hash is not None
                and row["content_hash"] != ref.content_hash):
            return _mismatch("content_hash")
        if (ref.parse_status
                and str(row["parse_status"]) != ref.parse_status):
            return _mismatch("parse_status")
        return _OK

    def validate_relation(
        self, ref: RelationRef
    ) -> EvidenceValidationResult:
        repo = self._repo_of_snapshot(ref.snapshot_id)
        if repo is None or repo != ref.repository_id:
            return EvidenceValidationResult(
                False, "cross-repo", "relação fora do repo/snapshot")
        row = self._conn.execute(
            "SELECT id, from_id, to_id, to_unresolved, type,"
            " resolution_status, confidence_class, resolver"
            " FROM relations WHERE id = ? AND snapshot_id = ?",
            (ref.relation_id, ref.snapshot_id)).fetchone()
        if row is None:
            return EvidenceValidationResult(
                False, "not-found",
                f"relation {ref.relation_id} ausente")
        if row["from_id"] != ref.from_occurrence:
            return EvidenceValidationResult(
                False, "bad-span", "origem da relação diverge")
        # lf-0058: endpoints/tipo/status/resolver/confiança declarados
        # são revalidados — aresta existe ≠ metadados corretos.
        if (ref.to_occurrence is not None
                and row["to_id"] != ref.to_occurrence):
            return _mismatch("to_occurrence")
        if (ref.to_unresolved is not None
                and row["to_unresolved"] != ref.to_unresolved):
            return _mismatch("to_unresolved")
        if ref.type and str(row["type"]) != ref.type:
            return _mismatch("type")
        if (ref.resolution_status
                and str(row["resolution_status"]) != ref.resolution_status):
            return _mismatch("resolution_status")
        if (ref.confidence_class
                and str(row["confidence_class"]) != ref.confidence_class):
            return _mismatch("confidence_class")
        if (ref.resolver is not None
                and row["resolver"] != ref.resolver):
            return _mismatch("resolver")
        return _OK

    def validate_symbol(
        self, ref: SymbolRef, snapshot_id: int
    ) -> EvidenceValidationResult:
        """Símbolo é repo-level: valida repo + identidade persistida."""
        repo = self._repo_of_snapshot(snapshot_id)
        if repo is None or repo != ref.repository_id:
            return EvidenceValidationResult(
                False, "cross-repo", "símbolo fora do repo")
        row = self._conn.execute(
            "SELECT qualified_name, kind, language"
            " FROM symbols WHERE id = ?",
            (ref.symbol_id,)).fetchone()
        if row is None:
            return EvidenceValidationResult(
                False, "not-found",
                f"symbol {ref.symbol_id} ausente")
        if ref.qualified_name and row["qualified_name"] != ref.qualified_name:
            return _mismatch("qualified_name")
        if ref.kind and str(row["kind"]) != ref.kind:
            return _mismatch("kind")
        if ref.language and str(row["language"]) != ref.language:
            return _mismatch("language")
        return _OK

    def validate_source_file(
        self, ref: SourceFileRef
    ) -> EvidenceValidationResult:
        repo = self._repo_of_snapshot(ref.snapshot_id)
        if repo is None or repo != ref.repository_id:
            return EvidenceValidationResult(
                False, "cross-repo", "arquivo fora do repo/snapshot")
        row = self._conn.execute(
            "SELECT relative_path, content_hash FROM source_files"
            " WHERE id = ? AND snapshot_id = ?",
            (ref.file_id, ref.snapshot_id)).fetchone()
        if row is None:
            return EvidenceValidationResult(
                False, "not-found",
                f"source_file {ref.file_id} ausente")
        if (ref.relative_path
                and str(row["relative_path"]) != ref.relative_path):
            return _mismatch("relative_path")
        if (ref.content_hash is not None
                and row["content_hash"] != ref.content_hash):
            return _mismatch("content_hash")
        return _OK

    def validate_snapshot(
        self, ref: SnapshotRef
    ) -> EvidenceValidationResult:
        repo = self._repo_of_snapshot(ref.snapshot_id)
        if repo is None:
            return EvidenceValidationResult(
                False, "not-found", "snapshot inexistente")
        if repo != ref.repository_id:
            return EvidenceValidationResult(
                False, "cross-repo",
                f"snapshot pertence ao repo {repo}, "
                f"ref declara {ref.repository_id}")
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
        # lf-0056: repository_id do caller deve bater com o dono REAL
        # do snapshot — sem essa checagem uma ref cross-repo recebia
        # identidade falsificada.
        srow = self._conn.execute(
            "SELECT repository_id FROM snapshots WHERE id = ?",
            (snapshot_id,)).fetchone()
        if srow is None or int(srow["repository_id"]) != repository_id:
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
