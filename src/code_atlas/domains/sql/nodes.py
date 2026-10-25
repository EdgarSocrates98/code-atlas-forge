"""AST mínimo do adaptador SQL (lf-0111, Cycle 7 Phase 3).

O modelo é **orientado a extração**, não a validação: carrega apenas o que
a montagem de entidades/relações precisa — nomes de tabelas, colrefs,
aliases, itens de SELECT e subqueries. Expressões nunca viram árvores
interpretáveis; ``ExprRefs`` guarda as referências observadas (colunas,
``*``, subqueries, marcador dinâmico) — suficiente para linhagem honesta.

Nomes normalizados: ``iname`` de palavras não-quotadas é lower-case;
``qident`` preserva case. Cada ``*Name`` guarda ``written`` (como
escrito) para evidência/diagnóstico.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from code_atlas.parsers.contracts import SourceSpan


@dataclass(frozen=True)
class NameRef:
    """Nome qualificado (``catalog.schema.table``) como escrito no SQL."""

    parts: tuple[str, ...]  # normalizado: word→lower, qident→case real
    written: str
    span: SourceSpan


@dataclass(frozen=True)
class ColRef:
    """Referência de coluna: 1..N partes (``a.b.c`` — aninhadas inclusas)."""

    parts: tuple[str, ...]
    span: SourceSpan


@dataclass(frozen=True)
class StarRef:
    """``*`` ou ``qualifier.*`` numa posição de expressão."""

    qualifier: tuple[str, ...]
    span: SourceSpan


@dataclass
class ExprRefs:
    """O que uma expressão referencia — coletado durante o scan.

    ``cols`` são colrefs visíveis; ``stars`` inclui ``count(*)`` (qualifier
    vazio); ``subqueries`` são selects aninhados encontrados na expressão
    (correlated ou não); ``dynamic`` marca presença de token de template.
    ``top_star`` é o star em nível zero (``*``/``alias.*``) — distingue
    item-estrela de ``count(*)`` (que tem star só em nível interno).
    """

    cols: list[ColRef] = field(default_factory=list)
    stars: list[StarRef] = field(default_factory=list)
    subqueries: list[SelectStmt] = field(default_factory=list)
    dynamic: bool = False
    top_star: StarRef | None = None

    def merge(self, other: ExprRefs) -> None:
        self.cols.extend(other.cols)
        self.stars.extend(other.stars)
        self.subqueries.extend(other.subqueries)
        self.dynamic = self.dynamic or other.dynamic
        if other.top_star is not None:
            self.top_star = other.top_star


@dataclass(frozen=True)
class SelectItem:
    """Item da lista SELECT: expressão + alias declarado."""

    refs: ExprRefs
    alias: str | None
    span: SourceSpan
    star: StarRef | None = None  # definido quando o item É `*`/`t.*`


@dataclass(frozen=True)
class Cte:
    """``name [ (cols) ] AS ( query )`` dentro de WITH."""

    name: str
    columns: tuple[str, ...]
    query: SelectStmt
    span: SourceSpan
    recursive: bool = False


# --- fontes do FROM ----------------------------------------------------


@dataclass(frozen=True)
class SrcTable:
    """Fonte nomeada (tabela/view/CTE a resolver)."""

    name: NameRef
    alias: str | None
    span: SourceSpan


@dataclass(frozen=True)
class SrcSub:
    """``( SELECT … ) [AS] alias [ (cols) ]`` — subquery derivada."""

    query: SelectStmt
    alias: str | None
    col_aliases: tuple[str, ...]
    span: SourceSpan


@dataclass(frozen=True)
class SrcGroup:
    """``( join … ) alias`` — fonte agrupada entre parênteses."""

    inner: Source
    alias: str | None
    span: SourceSpan


@dataclass(frozen=True)
class SrcJoin:
    """Join binário; ``kind`` preserva o texto do join para atributos."""

    left: Source
    right: Source
    kind: str
    span: SourceSpan


@dataclass(frozen=True)
class SrcValues:
    """``VALUES (…)`` em posição de fonte — dados literais, sem upstream."""

    alias: str | None
    col_aliases: tuple[str, ...]
    span: SourceSpan


@dataclass(frozen=True)
class SrcUnsupported:
    """Fonte não-tabela que o parser reconhece mas não expande.

    ``what``: ``unnest`` | ``lateral-view`` | ``table-function`` |
    ``dynamic`` — vira diagnóstico honesto na extração.
    """

    what: str
    written: str
    span: SourceSpan


Source = SrcTable | SrcSub | SrcGroup | SrcJoin | SrcValues | SrcUnsupported


@dataclass
class SelectStmt:
    """SELECT completo (um termo de UNION — branches ficam à parte)."""

    ctes: list[Cte]
    items: list[SelectItem]
    sources: list[Source]
    set_branches: list[SelectStmt]  # membros UNION/INTERSECT/EXCEPT
    set_ops: list[str]              # paralelo a set_branches
    tail_subqueries: list[SelectStmt]  # subs achados em WHERE/ON/exprs
    span: SourceSpan


# --- statements ----------------------------------------------------------


@dataclass
class Statement:
    kind: str            # select|insert|update|delete|merge|…|unsupported
    span: SourceSpan
    dynamic: bool = False


@dataclass
class SSelect(Statement):
    query: SelectStmt | None = None
    prepared_name: str = ""


@dataclass
class SInsert(Statement):
    target: NameRef | None = None
    columns: tuple[str, ...] | None = None
    query: SelectStmt | None = None
    value_rows: list[list[ExprRefs]] = field(default_factory=list)
    overwrite: bool = False


@dataclass
class SUpdate(Statement):
    target: NameRef | None = None
    target_alias: str | None = None
    sets: list[tuple[str, ExprRefs]] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    ctes: list[Cte] = field(default_factory=list)
    tail_subqueries: list[SelectStmt] = field(default_factory=list)


@dataclass
class SDelete(Statement):
    target: NameRef | None = None
    sources: list[Source] = field(default_factory=list)
    ctes: list[Cte] = field(default_factory=list)
    tail_subqueries: list[SelectStmt] = field(default_factory=list)


@dataclass
class SMerge(Statement):
    target: NameRef | None = None
    target_alias: str | None = None
    source: Source | None = None
    sets: list[tuple[str, ExprRefs]] = field(default_factory=list)
    insert_cols: tuple[str, ...] | None = None
    insert_exprs: list[ExprRefs] = field(default_factory=list)
    ctes: list[Cte] = field(default_factory=list)
    tail_subqueries: list[SelectStmt] = field(default_factory=list)


@dataclass
class SCreateTable(Statement):
    name: NameRef | None = None
    columns: list[tuple[str, str]] = field(default_factory=list)  # (nome, tipo)
    query: SelectStmt | None = None
    like: NameRef | None = None
    temporary: bool = False
    external: bool = False
    or_replace: bool = False


@dataclass
class SCreateView(Statement):
    name: NameRef | None = None
    col_aliases: tuple[str, ...] = ()
    query: SelectStmt | None = None
    or_replace: bool = False
    materialized: bool = False
    temporary: bool = False


@dataclass
class SUse(Statement):
    parts: tuple[str, ...] = ()
    written: str = ""


@dataclass
class STruncate(Statement):
    name: NameRef | None = None


@dataclass
class SUnsupported(Statement):
    """Statement reconhecido como fora do escopo de extração."""

    why: str = ""   # ex.: "drop", "alter", "dynamic-exec", "grant"
    head: str = ""  # primeira palavra do statement (diagnóstico)
