"""Contratos tipados do subsistema de parsing (Cycle 2 Phase 1).

Separação dura: parsing sintático ≠ resolução semântica. Tudo que um
adaptador produz é sintático — resolução de alvos é responsabilidade do
graph builder e de resolvers futuros (SCIP/LSP, Cycle 3).

Convenções de span: ``line`` é 1-based (humanos/editores), ``col`` é a
coluna em bytes 0-based do Tree-sitter; offsets em bytes sobre UTF-8.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class SourceSpan:
    """Localização de um nó no arquivo-fonte."""

    start_byte: int
    end_byte: int
    start_line: int  # 1-based
    start_col: int  # byte offset 0-based
    end_line: int
    end_col: int


@dataclass(frozen=True)
class ParseDiagnostic:
    """Problema estruturado registrado durante parsing/extração."""

    code: str  # ex.: "error-node", "missing-node", "file-too-large", "timeout"
    severity: str  # "error" | "warning" | "info"
    message: str
    span: SourceSpan | None = None


@dataclass(frozen=True)
class ParserCapability:
    """Capacidades declaradas de um adaptador (capability matrix)."""

    symbols: bool = True
    signatures: bool = True
    docstrings: bool = True
    imports: bool = True
    calls: bool = True
    decorators: bool = False
    inheritance: bool = True
    interfaces: bool = False


@dataclass(frozen=True)
class ParseRequest:
    """Entrada de parsing: bytes UTF-8 + metadados. Nunca executa o alvo."""

    path: str  # rel POSIX dentro do repositório
    language: str  # id resolvido pelo registry
    content: bytes
    timeout_us: int = 2_000_000


@dataclass(frozen=True)
class ExtractedSymbol:
    """Símbolo estrutural extraído — ligação lógica fica para o grafo."""

    language: str
    kind: str  # NodeKind string: module|class|interface|function|method|symbol
    name: str
    qualified_name: str  # module.scope.name — nunca usa nº de linha
    span: SourceSpan
    name_span: SourceSpan | None = None
    scope_path: tuple[str, ...] = ()  # nomes dos escopos léxicos externos
    signature: str | None = None
    params: tuple[str, ...] = ()  # nomes dos parâmetros, em ordem
    docstring: str | None = None
    decorators: tuple[str, ...] = ()
    bases: tuple[str, ...] = ()  # extends declarado
    implements: tuple[str, ...] = ()  # implements declarado (TS)
    modifiers: tuple[str, ...] = ()  # async|export|default|static|abstract...


@dataclass(frozen=True)
class ExtractedReference:
    """Menção sintática a um nome — alvo textual preservado."""

    name: str  # texto do alvo (ex.: "foo", "pkg.mod", "obj.method")
    context: str  # call|import|import-member|extends|implements|decorator|type-ref|jsx
    span: SourceSpan
    scope_path: tuple[str, ...] = ()
    target: str = ""  # import-member: "modulo.membro" do símbolo importado


@dataclass(frozen=True)
class ExtractedRelation:
    """Relação sintática candidata extraída pelo adaptador."""

    type: str  # EdgeKind string
    from_name: str  # qualified name da origem
    to_name: str  # alvo textual — resolução é do graph builder
    span: SourceSpan
    confidence_class: str = "syntactic"
    resolution_status: str = "unresolved"


@dataclass(frozen=True)
class ParseResult:
    """Saída de um arquivo parseado — real, sem mocks."""

    path: str
    language: str
    parser: str  # id do adaptador (ex.: "tree-sitter-python")
    parser_version: str
    grammar_version: str
    content_hash: str  # sha256 do conteúdo — casa com a evidência
    has_error: bool = False
    error_node_count: int = 0
    missing_node_count: int = 0
    node_count: int = 0
    diagnostics: tuple[ParseDiagnostic, ...] = ()
    symbols: tuple[ExtractedSymbol, ...] = ()
    references: tuple[ExtractedReference, ...] = ()
    relations: tuple[ExtractedRelation, ...] = ()
    supported: bool = True


class ParserAdapter(Protocol):
    """Adaptador concreto de linguagem (implementado por Tree-sitter)."""

    language: str
    extensions: tuple[str, ...]
    capabilities: ParserCapability

    @property
    def adapter_id(self) -> str:
        """Ex.: ``tree-sitter-python`` — entra em analyzer_versions."""
        ...

    @property
    def parser_version(self) -> str: ...

    @property
    def grammar_version(self) -> str: ...

    def parse(self, request: ParseRequest) -> ParseResult: ...


@dataclass(frozen=True)
class EvidenceRecord:
    """Proveniência de uma relação/nó persistido (Blueprint §5)."""

    path: str  # arquivo de origem
    span: SourceSpan | None
    content_hash: str
    extractor: str  # adapter_id
    extractor_version: str  # "parserX.Y+grammarZ.W"
    notes: str = ""


@dataclass(frozen=True)
class GraphNode:
    """Nó tipado do grafo canônico (saída do builder)."""

    node_id: str  # identidade determinística — nunca nº de linha
    kind: str  # NodeKind
    name: str
    qualified_name: str
    language: str = ""
    path: str = ""  # rel path (file/module/package nodes)
    span: SourceSpan | None = None
    content_hash: str = ""
    signature: str | None = None
    params: tuple[str, ...] = ()
    docstring: str | None = None
    modifiers: tuple[str, ...] = ()


@dataclass(frozen=True)
class GraphEdge:
    """Aresta tipada com proveniência completa."""

    type: str  # EdgeKind
    from_id: str  # GraphNode.node_id
    to_id: str | None = None  # None quando alvo não resolveu para nó
    to_text: str = ""  # alvo textual preservado (unresolved/ambiguous)
    resolution_status: str = "unresolved"
    confidence_class: str = "syntactic"
    resolver: str = ""  # id do resolver/extrator
    evidence_index: tuple[int, ...] = ()  # índices em GraphBuildResult.evidence
    span: SourceSpan | None = None
    source_path: str = ""  # arquivo de origem da aresta (lf-0027)


@dataclass(frozen=True)
class GraphBuildResult:
    """Saída determinística do graph builder (hand-off para storage)."""

    nodes: tuple[GraphNode, ...] = ()
    edges: tuple[GraphEdge, ...] = ()
    evidence: tuple[EvidenceRecord, ...] = ()
    diagnostics: tuple[ParseDiagnostic, ...] = ()
    stats: dict[str, int] = field(default_factory=dict)
