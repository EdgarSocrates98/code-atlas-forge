"""Importador de índices SCIP — bytes protobuf → modelo tipado (lf-0017).

Cobertura do schema (scip.proto oficial):
- Index{metadata, documents, external_symbols}
- Metadata{version, tool_info, project_root, text_document_encoding}
- Document{language, relative_path, occurrences, symbols, position_encoding}
- Occurrence{range(deprec.), single_line_range, multi_line_range, symbol,
  symbol_roles, override_documentation}
- SymbolInformation{symbol, documentation, relationships, kind}
- Relationship{symbol, is_reference, is_implementation, is_type_definition,
  is_definition}
Campos não listados são skipados preservando wire type (forward-compat).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from code_atlas.resolvers.scip.decoder import DecodeError, decode_message

SpecMap = dict[int, tuple[str, Any]]

ROLE_DEFINITION = 0x1
ROLE_IMPORT = 0x2
ROLE_WRITE_ACCESS = 0x4
ROLE_READ_ACCESS = 0x8
ROLE_GENERATED = 0x10
ROLE_TEST = 0x20
ROLE_FORWARD = 0x1000000

TOOL_INFO_SPEC: SpecMap = {
    1: ("name", "string"),
    2: ("version", "string"),
    3: ("arguments", ("repeated", "string")),
}
METADATA_SPEC: SpecMap = {
    1: ("version", "varint"),
    2: ("tool_info", (TOOL_INFO_SPEC, "message")),
    3: ("project_root", "string"),
    4: ("text_document_encoding", "varint"),
}
SLR_SPEC: SpecMap = {
    1: ("line", "int32"),
    2: ("start_character", "int32"),
    3: ("end_character", "int32"),
}
MLR_SPEC: SpecMap = {
    1: ("start_line", "int32"),
    2: ("start_character", "int32"),
    3: ("end_line", "int32"),
    4: ("end_character", "int32"),
}
OCCURRENCE_SPEC: SpecMap = {
    1: ("range", ("repeated", "int32")),
    2: ("symbol", "string"),
    3: ("symbol_roles", "int32"),
    4: ("override_documentation", ("repeated", "string")),
    5: ("syntax_kind", "varint"),
    6: ("diagnostics", ("repeated", ("repeated", "string"))),  # skip-ish
    7: ("enclosing_range", ("repeated", "int32")),
    8: ("single_line_range", (SLR_SPEC, "message")),
    9: ("multi_line_range", (MLR_SPEC, "message")),
}
RELATIONSHIP_SPEC: SpecMap = {
    1: ("symbol", "string"),
    2: ("is_reference", "bool"),
    3: ("is_implementation", "bool"),
    4: ("is_type_definition", "bool"),
    5: ("is_definition", "bool"),
}
SYMBOL_INFO_SPEC: SpecMap = {
    1: ("symbol", "string"),
    3: ("documentation", ("repeated", "string")),
    4: ("relationships", ("repeated", (RELATIONSHIP_SPEC, "message"))),
    5: ("kind", "varint"),
    6: ("display_name", "string"),
    7: ("signature_documentation", ("repeated", "string")),
    9: ("enclosing_symbol", "string"),
}
DOCUMENT_SPEC: SpecMap = {
    1: ("relative_path", "string"),
    2: ("occurrences", ("repeated", (OCCURRENCE_SPEC, "message"))),
    3: ("symbols", ("repeated", (SYMBOL_INFO_SPEC, "message"))),
    4: ("language", "string"),
    5: ("text", "string"),
    6: ("position_encoding", "varint"),
}
INDEX_SPEC: SpecMap = {
    1: ("metadata", (METADATA_SPEC, "message")),
    2: ("documents", ("repeated", (DOCUMENT_SPEC, "message"))),
    3: ("external_symbols", ("repeated", (SYMBOL_INFO_SPEC, "message"))),
}


@dataclass(frozen=True)
class ScipRange:
    """Range normalizado (sempre multilinha: start/end line+char)."""

    start_line: int
    start_char: int
    end_line: int
    end_char: int


@dataclass(frozen=True)
class ScipOccurrence:
    symbol: str
    roles: int
    range: ScipRange | None

    @property
    def is_definition(self) -> bool:
        return bool(self.roles & ROLE_DEFINITION)


@dataclass(frozen=True)
class ScipRelationship:
    symbol: str
    is_reference: bool = False
    is_implementation: bool = False
    is_type_definition: bool = False
    is_definition: bool = False


@dataclass(frozen=True)
class ScipSymbolInfo:
    symbol: str
    kind: int = 0
    documentation: tuple[str, ...] = ()
    relationships: tuple[ScipRelationship, ...] = ()
    enclosing_symbol: str = ""


@dataclass(frozen=True)
class ScipDocument:
    relative_path: str
    language: str
    occurrences: tuple[ScipOccurrence, ...] = ()
    symbols: tuple[ScipSymbolInfo, ...] = ()
    position_encoding: int = 0


@dataclass(frozen=True)
class ScipIndex:
    """Índice SCIP decodificado — modelo interno do Atlas."""

    project_root: str = ""
    tool_name: str = ""
    tool_version: str = ""
    text_document_encoding: int = 0
    documents: tuple[ScipDocument, ...] = ()
    external_symbols: tuple[ScipSymbolInfo, ...] = ()
    raw_metadata: dict[str, Any] = field(default_factory=dict)


def _to_range(msg: dict[str, Any]) -> ScipRange | None:
    slr = msg.get("single_line_range")
    if slr is not None:
        return ScipRange(
            start_line=slr.get("line", 0),
            start_char=slr.get("start_character", 0),
            end_line=slr.get("line", 0),
            end_char=slr.get("end_character", 0),
        )
    mlr = msg.get("multi_line_range")
    if mlr is not None:
        return ScipRange(
            start_line=mlr.get("start_line", 0),
            start_char=mlr.get("start_character", 0),
            end_line=mlr.get("end_line", 0),
            end_char=mlr.get("end_character", 0),
        )
    rng = msg.get("range") or []
    if len(rng) >= 4:
        return ScipRange(rng[0], rng[1], rng[2], rng[3])
    if len(rng) == 3:
        return ScipRange(rng[0], rng[1], rng[0], rng[2])
    return None


def _to_relationship(msg: dict[str, Any]) -> ScipRelationship:
    return ScipRelationship(
        symbol=msg.get("symbol", ""),
        is_reference=bool(msg.get("is_reference", False)),
        is_implementation=bool(msg.get("is_implementation", False)),
        is_type_definition=bool(msg.get("is_type_definition", False)),
        is_definition=bool(msg.get("is_definition", False)),
    )


def _to_symbol_info(msg: dict[str, Any]) -> ScipSymbolInfo:
    return ScipSymbolInfo(
        symbol=msg.get("symbol", ""),
        kind=msg.get("kind", 0),
        documentation=tuple(msg.get("documentation", [])),
        relationships=tuple(
            _to_relationship(r) for r in msg.get("relationships", [])),
        enclosing_symbol=msg.get("enclosing_symbol", ""),
    )


def _to_document(msg: dict[str, Any]) -> ScipDocument:
    return ScipDocument(
        relative_path=msg.get("relative_path", ""),
        language=msg.get("language", ""),
        occurrences=tuple(
            ScipOccurrence(
                symbol=o.get("symbol", ""),
                roles=o.get("symbol_roles", 0),
                range=_to_range(o),
            )
            for o in msg.get("occurrences", [])
        ),
        symbols=tuple(_to_symbol_info(s) for s in msg.get("symbols", [])),
        position_encoding=msg.get("position_encoding", 0),
    )


def parse_index(data: bytes) -> ScipIndex:
    """Decodifica bytes protobuf de um .scip → ScipIndex tipado."""
    msg = decode_message(data, INDEX_SPEC, path="Index")
    meta = msg.get("metadata") or {}
    tool = meta.get("tool_info") or {}
    docs = [_to_document(d) for d in msg.get("documents", [])]
    return ScipIndex(
        project_root=meta.get("project_root", ""),
        tool_name=tool.get("name", ""),
        tool_version=tool.get("version", ""),
        text_document_encoding=meta.get("text_document_encoding", 0),
        documents=tuple(docs),
        external_symbols=tuple(
            _to_symbol_info(s) for s in msg.get("external_symbols", [])),
        raw_metadata=meta,
    )


__all__ = [
    "ROLE_DEFINITION",
    "DecodeError",
    "ScipDocument",
    "ScipIndex",
    "ScipOccurrence",
    "ScipRange",
    "ScipRelationship",
    "ScipSymbolInfo",
    "parse_index",
]
