"""Parser SQL descendente-recursivo em stdlib puro (lf-0111, Phase 3).

Cobre o subconjunto declarativo necessário à linhagem: SELECT (com CTE,
joins, subqueries, set-ops), INSERT (INTO/OVERWRITE + col list +
PARTITION), UPDATE/DELETE/MERGE, CREATE TABLE (DDL + CTAS + LIKE),
CREATE VIEW, USE, TRUNCATE e PREPARE. O restante vira ``SUnsupported``.

Dialeto é **rótulo**, não gate: o parser é permissivo e aceita sintaxes
Athena/Trino (UNNEST→unsupported-source, ``->`` lambda, ``WITH (props)``)
e Spark (INSERT OVERWRITE, LATERAL VIEW→unsupported-source, DISTRIBUTE/
CLUSTER/SORT BY, ``${}``→token dynamic) onde elas divergem de ANSI.

Disciplina de erro: ``ParseError`` interno → diagnóstico + resync no
próximo ``;`` top-level — extração parcial, nunca crash. ``parse_file``
é o único ponto de entrada e nunca levanta.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field

from code_atlas.domains.sql.nodes import (
    ColRef,
    Cte,
    ExprRefs,
    NameRef,
    SCreateTable,
    SCreateView,
    SDelete,
    SelectItem,
    SelectStmt,
    SInsert,
    SMerge,
    Source,
    SrcGroup,
    SrcJoin,
    SrcSub,
    SrcTable,
    SrcUnsupported,
    SrcValues,
    SSelect,
    StarRef,
    Statement,
    STruncate,
    SUnsupported,
    SUpdate,
    SUse,
)
from code_atlas.domains.sql.tokens import (
    _SPECIAL_ARG_FNS,
    _TYPE_LITERAL_WORDS,
    Token,
    is_kw,
    is_name,
    is_word,
    tokenize,
)
from code_atlas.parsers.contracts import ParseDiagnostic, SourceSpan

_MAX_DEPTH = 32  # subqueries/parênteses — além disso é patológico

# Keywords que terminam qualquer expressão em depth 0. Inclui inícios de
# cláusula e de statement — um segundo SELECT no meio de uma expressão é
# erro de sincronização, não continuação.
_EXPR_STOPS: frozenset[str] = frozenset(
    {
        "FROM", "WHERE", "GROUP", "HAVING", "QUALIFY", "WINDOW", "ORDER",
        "LIMIT", "OFFSET", "FETCH", "UNION", "INTERSECT", "EXCEPT",
        "INTO", "ON", "USING", "JOIN", "INNER", "LEFT", "RIGHT", "FULL",
        "CROSS", "NATURAL", "SEMI", "ANTI", "LATERAL", "PIVOT",
        "TABLESAMPLE", "WHEN", "THEN", "ELSE", "END", "AS", "PARTITION",
        "CLUSTER", "DISTRIBUTE", "SORT", "MATCHED", "SET", "VALUES",
        "SELECT", "INSERT", "UPDATE", "DELETE", "MERGE", "CREATE", "USE",
        "DROP", "ALTER", "GRANT", "REVOKE", "TRUNCATE", "SHOW",
        "DESCRIBE", "EXPLAIN", "ANALYZE", "MSCK", "CACHE",
        "UNCACHE", "REFRESH", "EXEC", "EXECUTE", "CALL", "PREPARE",
        "DEALLOCATE", "COMMIT", "ROLLBACK", "VACUUM", "OPTIMIZE",
        "LOAD", "EXPORT", "IMPORT",
    }
)

_ITEM_STOPS = _EXPR_STOPS | frozenset({"AS"})

_UNSUPPORTED_START: frozenset[str] = frozenset(
    {
        "DROP", "ALTER", "GRANT", "REVOKE", "ANALYZE", "EXPLAIN",
        "DESCRIBE", "DESC", "SHOW", "MSCK", "CACHE", "UNCACHE",
        "REFRESH", "SET", "RESET", "ADD", "JAR", "LOAD", "EXPORT",
        "IMPORT", "VACUUM", "OPTIMIZE", "COMMIT", "ROLLBACK", "BEGIN",
        "START", "TRANSACTION", "DEALLOCATE",
    }
)

_DYNAMIC_START: frozenset[str] = frozenset({"EXEC", "EXECUTE", "CALL"})

_CREATE_SUB: frozenset[str] = frozenset(
    {
        "SCHEMA", "DATABASE", "INDEX", "FUNCTION", "PROCEDURE", "ROLE",
        "TRIGGER", "SEQUENCE",
    }
)

_CONSTRAINT_HEAD: frozenset[str] = frozenset(
    {"PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT", "INDEX", "KEY"}
)

_INTERVAL_UNITS: frozenset[str] = frozenset(
    {
        "YEAR", "QUARTER", "MONTH", "WEEK", "DAY", "HOUR", "MINUTE",
        "SECOND", "MILLISECOND", "TO",
    }
)

_FN_FIELD_WORDS: frozenset[str] = frozenset(
    {
        "BOTH", "LEADING", "TRAILING", "YEAR", "QUARTER", "MONTH", "WEEK",
        "DAY", "HOUR", "MINUTE", "SECOND", "EPOCH", "TIMEZONE_HOUR",
        "TIMEZONE_MINUTE", "TIMEZONE_ABBR", "DOW", "DOY", "CENTURY",
        "DECADE", "MILLENNIUM", "ISOYEAR",
    }
)

# Padrões de rótulo de dialeto — heurística declarada, nunca gate.
_SPARK_RE = re.compile(
    r"\b(?:insert\s+overwrite|lateral\s+view|distribute\s+by|cluster\s+by"
    r"|msck\s+repair|tblproperties|stored\s+as|bucketed\s+by"
    r"|using\s+(?:delta|parquet|orc|json|csv|hive|iceberg))\b|\$\{",
    re.IGNORECASE | re.DOTALL,
)
_ATHENA_RE = re.compile(
    r"\b(?:unnest|try_cast|approx_\w+|cardinality|external_location"
    r"|partitioned_by|bucketed_by|ordinality)\b|->",
    re.IGNORECASE,
)


def detect_dialect(text: str) -> str:
    """Rótulo heurístico do dialeto dominante do arquivo.

    Retorna ``spark`` | ``athena`` | ``ansi``. É metadata honesta de
    discovery — o parser não muda comportamento por ela.
    """
    if _SPARK_RE.search(text):
        return "spark"
    if _ATHENA_RE.search(text):
        return "athena"
    return "ansi"


class ParseError(Exception):
    """Erro interno de parse — vira diagnóstico, nunca propaga."""

    def __init__(self, msg: str, tok: Token) -> None:
        super().__init__(msg)
        self.msg = msg
        self.tok = tok


class _Src:
    """Mapeia offsets de caractere → ``SourceSpan`` (bytes, linha, col)."""

    def __init__(self, text: str) -> None:
        self._lines = [0]
        for m in re.finditer("\n", text):
            self._lines.append(m.end())
        self._bytes: list[int] | None = None
        if not text.isascii():
            offs: list[int] = []
            b = 0
            for ch in text:
                offs.append(b)
                b += len(ch.encode("utf-8"))
            offs.append(b)
            self._bytes = offs

    def _b(self, i: int) -> int:
        if self._bytes is None:
            return i
        return self._bytes[min(i, len(self._bytes) - 1)]

    def span(self, start: int, end: int) -> SourceSpan:
        """``start``/``end`` são offsets de CARACTERE."""
        li = bisect.bisect_right(self._lines, start) - 1
        le = bisect.bisect_right(self._lines, end) - 1
        return SourceSpan(
            start_byte=self._b(start),
            end_byte=self._b(end),
            start_line=li + 1,
            start_col=self._b(start) - self._b(self._lines[li]),
            end_line=le + 1,
            end_col=self._b(end) - self._b(self._lines[le]),
        )


@dataclass
class ParsedFile:
    statements: list[Statement]
    diagnostics: list[ParseDiagnostic]
    dialect: str
    problems: list[str] = field(default_factory=list)


@dataclass
class _ExprScan:
    """Estado mutável do scan de expressão (``_P.expr`` + helpers)."""

    refs: ExprRefs = field(default_factory=ExprRefs)
    tagged: list[tuple[ColRef, int]] = field(default_factory=list)
    bound: dict[int, set[str]] = field(default_factory=dict)
    recent: list[str] = field(default_factory=list)
    lvl: int = 0
    case_depth: int = 0
    value_end: bool = False


def parse_file(text: str) -> ParsedFile:
    """Tokeniza + parseia; nunca levanta — problemas viram diagnósticos."""
    toks, problems = tokenize(text)
    src = _Src(text)
    p = _P(toks, src)
    diags: list[ParseDiagnostic] = [
        ParseDiagnostic(code=prob, severity="warning", message=prob)
        for prob in problems
    ]
    dialect = detect_dialect(text)
    statements: list[Statement] = []
    while not p.at_eof():
        if p.accept_punct(";"):
            continue
        start = p.i
        try:
            st = p.statement()
        except ParseError as exc:
            diags.append(
                ParseDiagnostic(
                    code="parse-error",
                    severity="error",
                    message=f"{exc.msg} (near {exc.tok.text!r})",
                    span=src.span(exc.tok.start, exc.tok.end),
                )
            )
            p.resync()
            continue
        if st is not None:
            st.dynamic = st.dynamic or p.had_dynamic(start, p.i)
            statements.append(st)
        # tokens não consumidos antes do próximo `;` → resíduo honesto
        if not p.at_eof() and not p.at_punct(";"):
            trailing = p.cur()
            diags.append(
                ParseDiagnostic(
                    code="trailing-tokens",
                    severity="warning",
                    message=f"unparsed trailing tokens (near {trailing.text!r})",
                    span=src.span(trailing.start, trailing.end),
                )
            )
            p.resync()
    return ParsedFile(
        statements=statements, diagnostics=diags,
        dialect=dialect, problems=problems,
    )


class _P:
    """Cursor de tokens com helpers de match/span."""

    def __init__(self, toks: list[Token], src: _Src) -> None:
        self.toks = toks
        self.i = 0
        self.src = src

    # --- cursor -----------------------------------------------------

    def cur(self) -> Token:
        return self.toks[self.i]

    def peek(self, k: int = 1) -> Token:
        j = self.i + k
        return self.toks[min(j, len(self.toks) - 1)]

    def at_eof(self) -> bool:
        return self.toks[self.i].kind == "eof"

    def at_punct(self, s: str) -> bool:
        t = self.toks[self.i]
        return t.kind == "punct" and t.text == s

    def at_kw(self, *words: str) -> bool:
        return is_kw(self.toks[self.i], *words)

    def at_name(self) -> bool:
        return is_name(self.toks[self.i])

    def at(self, idx: int, *words: str) -> bool:
        """``at_kw`` em posição arbitrária (lookahead)."""
        return is_kw(self.toks[min(idx, len(self.toks) - 1)], *words)

    def accept_punct(self, s: str) -> bool:
        if self.at_punct(s):
            self.i += 1
            return True
        return False

    def accept_kw(self, *words: str) -> bool:
        if self.at_kw(*words):
            self.i += 1
            return True
        return False

    def expect_kw(self, *words: str) -> Token:
        t = self.toks[self.i]
        if not is_kw(t, *words):
            raise ParseError(f"expected {'/'.join(words)}", t)
        self.i += 1
        return t

    def expect_punct(self, s: str) -> Token:
        t = self.toks[self.i]
        if t.kind != "punct" or t.text != s:
            raise ParseError(f"expected {s!r}", t)
        self.i += 1
        return t

    def span(self, start: int, end: int) -> SourceSpan:
        """Offsets de caractere → SourceSpan em bytes."""
        return self.src.span(start, end)

    def last_end(self) -> int:
        """Offset char do fim do último token consumido."""
        return self.toks[self.i - 1].end if self.i else 0

    def had_dynamic(self, start: int, end: int) -> bool:
        return any(t.kind == "dynamic" for t in self.toks[start:end])

    def resync(self) -> None:
        """Avança até o próximo ``;`` top-level (ou EOF)."""
        depth = 0
        while not self.at_eof():
            t = self.cur()
            if t.kind == "punct":
                if t.text == "(":
                    depth += 1
                elif t.text == ")":
                    depth = max(0, depth - 1)
                elif t.text == ";" and depth == 0:
                    return
            self.i += 1

    def skip_balanced(self) -> None:
        """Consome ``( … )`` balanceado; erro se não abre com ``(``."""
        self.expect_punct("(")
        depth = 1
        while depth > 0:
            if self.at_eof():
                raise ParseError("unbalanced parens", self.cur())
            t = self.cur()
            if t.kind == "punct":
                if t.text == "(":
                    depth += 1
                elif t.text == ")":
                    depth -= 1
            self.i += 1

    # --- nomes --------------------------------------------------------

    def name_chain(self, *, loose: bool = False) -> NameRef:
        """``part(.part)*`` — parts normalizadas (word→lower, qident→case).

        ``loose=True`` aceita keyword como primeira parte (USE/CREATE com
        nomes como ``default``); caso contrário a primeira parte deve ser
        nome não-keyword.
        """
        start = self.cur()
        t = self.cur()
        if not is_word(t):
            raise ParseError("expected name", t)
        if not loose and not is_name(t):
            raise ParseError("expected identifier (reserved word)", t)
        parts = [t.iname]
        written = [t.text]
        end = t.end
        self.i += 1
        while self.at_punct(".") and is_word(self.peek()):
            self.i += 1  # '.'
            t = self.cur()
            parts.append(t.iname)
            written.append(t.text)
            end = t.end
            self.i += 1
        return NameRef(
            parts=tuple(parts),
            written=".".join(written),
            span=self.span(start.start, end),
        )

    def opt_alias(self, *, cols: bool = False) -> tuple[str | None, tuple[str, ...]]:
        """Alias opcional: ``AS name`` ou nome implícito.

        ``cols=True`` tenta ``( a, b )`` de col-aliases logo após o
        alias — restaura a posição quando não é lista de nomes.
        """
        alias: str | None = None
        col_aliases: tuple[str, ...] = ()
        if self.accept_kw("AS"):
            t = self.cur()
            if not is_word(t):
                raise ParseError("expected alias name", t)
            alias = t.iname
            self.i += 1
        elif self.at_name():
            alias = self.cur().iname
            self.i += 1
        if cols and alias is not None and self.at_punct("("):
            mark = self.i
            try:
                col_aliases = self._col_list()
            except ParseError:
                self.i = mark  # era outra coisa — restaura
        return alias, col_aliases

    def _col_list(self) -> tuple[str, ...]:
        """``( nome, … )`` — apenas nomes; erro em qualquer outra coisa."""
        self.expect_punct("(")
        names: list[str] = []
        while True:
            t = self.cur()
            if not is_word(t):
                raise ParseError("expected column name", t)
            names.append(t.iname)
            self.i += 1
            if self.accept_punct(","):
                continue
            self.expect_punct(")")
            return tuple(names)

    # --- statements ---------------------------------------------------

    def statement(self) -> Statement | None:
        ctes: list[Cte] = []
        start = self.cur()
        if self.at_kw("WITH"):
            ctes = self.with_clause()
        t = self.cur()
        if t.kind == "dynamic":
            raise ParseError("dynamic/template SQL at statement start", t)
        if is_kw(t, "SELECT"):
            q = self.query(ctes)
            return SSelect(
                kind="select",
                span=self.span(start.start, self.last_end()),
                query=q,
            )
        if is_kw(t, "INSERT", "UPDATE", "DELETE", "MERGE", "CREATE"):
            return self._statement_dml(t, ctes, start)
        return self._statement_aux(t, start)

    def _statement_dml(
        self, t: Token, ctes: list[Cte], start: Token
    ) -> Statement:
        """DML + CREATE — ``t`` é a keyword de início do statement."""
        if is_kw(t, "INSERT"):
            return self.insert_stmt(ctes, start)
        if is_kw(t, "UPDATE"):
            return self.update_stmt(ctes, start)
        if is_kw(t, "DELETE"):
            return self.delete_stmt(ctes, start)
        if is_kw(t, "MERGE"):
            return self.merge_stmt(ctes, start)
        if ctes:
            raise ParseError("WITH before CREATE is not supported", t)
        return self.create_stmt(start)

    def _statement_aux(self, t: Token, start: Token) -> Statement:
        """USE/TRUNCATE/PREPARE + dinâmico/unsupported — ``t`` é a head."""
        if is_kw(t, "USE"):
            self.i += 1
            name = self.name_chain(loose=True)
            return SUse(
                kind="use", span=self.span(start.start, self.last_end()),
                parts=name.parts, written=name.written,
            )
        if is_kw(t, "TRUNCATE"):
            self.i += 1
            self.accept_kw("TABLE")
            name = self.name_chain(loose=True)
            return STruncate(
                kind="truncate",
                span=self.span(start.start, self.last_end()),
                name=name,
            )
        if is_kw(t, "PREPARE"):
            return self.prepare_stmt(start)
        if is_kw(t, *_DYNAMIC_START):
            st = SUnsupported(
                kind="unsupported", span=self.span(start.start, t.end),
                why="dynamic-exec", head=t.norm,
            )
            self.skip_to_boundary()
            return st
        if is_kw(t, *_UNSUPPORTED_START):
            st = SUnsupported(
                kind="unsupported", span=self.span(start.start, t.end),
                why=t.norm.lower(), head=t.norm,
            )
            self.skip_to_boundary()
            return st
        raise ParseError("unrecognized statement start", t)

    def skip_to_boundary(self) -> None:
        """Consome até ``;`` top-level — para statements não-parseados."""
        self.resync()

    def with_clause(self) -> list[Cte]:
        self.expect_kw("WITH")
        recursive = self.accept_kw("RECURSIVE")
        ctes: list[Cte] = []
        while True:
            t = self.cur()
            if not is_word(t):
                raise ParseError("expected CTE name", t)
            name = t.iname
            self.i += 1
            cols: tuple[str, ...] = ()
            if self.at_punct("("):
                cols = self._col_list()
            self.expect_kw("AS")
            self.accept_kw("NOT")
            self.accept_kw("MATERIALIZED")
            self.expect_punct("(")
            q = self.query()
            self.expect_punct(")")
            ctes.append(
                Cte(
                    name=name, columns=cols, query=q,
                    span=self.span(t.start, self.last_end()),
                    recursive=recursive,
                )
            )
            if not self.accept_punct(","):
                break
        return ctes

    # --- SELECT ---------------------------------------------------------

    def query(self, ctes: list[Cte] | None = None) -> SelectStmt:
        """select_term (set_op select_term)* — parênteses nos termos."""
        if self.at_kw("WITH"):
            ctes = [*self.with_clause(), *(ctes or [])]
        first = self.query_term()
        if ctes:
            first.ctes = list(ctes) + first.ctes
        while self.at_kw("UNION", "INTERSECT", "EXCEPT"):
            op = self.cur().norm
            self.i += 1
            if self.accept_kw("ALL"):
                op += " ALL"
            else:
                self.accept_kw("DISTINCT")
            first.set_ops.append(op)
            first.set_branches.append(self.query_term())
        return first

    def query_term(self) -> SelectStmt:
        if self.at_punct("("):
            self.i += 1
            s = self.query()
            self.expect_punct(")")
            return s
        return self.select_core()

    def select_core(self) -> SelectStmt:
        start = self.cur()
        self.expect_kw("SELECT")
        self.accept_kw("ALL")
        self.accept_kw("DISTINCT")
        if self.accept_kw("TOP"):
            # TOP n — quantificador não-ansi; consome um token.
            if self.at_punct("("):
                self.skip_balanced()
            elif not self.at_eof():
                self.i += 1
        items: list[SelectItem] = []
        subs: list[SelectStmt] = []
        if (
            self.at_kw("FROM") or self.at_punct(";") or self.at_eof()
            or self.at_punct(")")
        ):
            raise ParseError("empty select list", self.cur())
        while True:
            items.append(self.select_item(subs))
            if self.accept_punct(","):
                continue
            break
        sources: list[Source] = []
        if self.accept_kw("FROM"):
            sources = self.source_list(subs)
        self.tail_clauses(subs)
        return SelectStmt(
            ctes=[], items=items, sources=sources, set_branches=[],
            set_ops=[], tail_subqueries=subs,
            span=self.span(start.start, self.last_end()),
        )

    def tail_clauses(self, subs: list[SelectStmt]) -> None:
        """WHERE/GROUP/HAVING/QUALIFY/ORDER/LIMIT… — scan sem linhagem."""
        while True:
            if self.accept_kw("WHERE", "HAVING", "QUALIFY", "WINDOW"):
                refs = self.expr(_EXPR_STOPS)
                subs.extend(refs.subqueries)
            elif self.accept_kw("GROUP", "ORDER"):
                self.accept_kw("BY")
                refs = self.expr(_EXPR_STOPS)
                subs.extend(refs.subqueries)
                while self.accept_punct(","):
                    refs = self.expr(_EXPR_STOPS)
                    subs.extend(refs.subqueries)
            elif self.at_kw("CLUSTER", "DISTRIBUTE", "SORT") and (
                self.peek().kind == "word" and self.peek().norm == "BY"
            ):
                self.i += 2  # kw + BY
                refs = self.expr(_EXPR_STOPS)
                subs.extend(refs.subqueries)
            elif self.at_kw("LIMIT", "OFFSET", "FETCH"):
                self.i += 1
                self.limit_spec()
            else:
                return

    def limit_spec(self) -> None:
        """Consome ``LIMIT n[,m]|ALL|OFFSET n ROWS|FETCH NEXT n …``."""
        while not self.at_eof():
            t = self.cur()
            if t.kind == "number" or is_kw(
                t, "ALL", "ROWS", "ROW", "ONLY", "WITH", "TIES",
                "FIRST", "NEXT", "OFFSET", "PERCENT",
            ):
                self.i += 1
                continue
            if self.accept_punct(","):
                continue
            return

    def select_item(self, subs: list[SelectStmt]) -> SelectItem:
        start = self.cur()
        if self.at_punct("*"):
            self.i += 1
            star = StarRef(qualifier=(), span=self.span(start.start, start.end))
            return SelectItem(
                refs=ExprRefs(), alias=None,
                span=self.span(start.start, start.end), star=star,
            )
        refs = self.expr(_ITEM_STOPS)
        subs.extend(refs.subqueries)
        alias: str | None = None
        if self.accept_kw("AS"):
            t = self.cur()
            if not is_word(t):
                raise ParseError("expected alias", t)
            alias = t.iname
            self.i += 1
        elif self.at_name():
            alias = self.cur().iname
            self.i += 1
        star_ref = refs.top_star if not refs.cols else None
        return SelectItem(
            refs=refs, alias=alias,
            span=self.span(start.start, self.last_end()), star=star_ref,
        )

    # --- FROM ---------------------------------------------------------

    def source_list(self, subs: list[SelectStmt]) -> list[Source]:
        srcs = [self.join_chain(subs)]
        while self.accept_punct(","):
            srcs.append(self.join_chain(subs))
        return srcs

    _JOIN_MODS = frozenset(
        {
            "INNER", "LEFT", "RIGHT", "FULL", "OUTER", "CROSS",
            "NATURAL", "SEMI", "ANTI",
        }
    )

    def join_chain(self, subs: list[SelectStmt], depth: int = 0) -> Source:
        start = self.cur()
        left = self.primary(subs, depth)
        while True:
            t = self.cur()
            if is_kw(t, "JOIN"):
                kind = "JOIN"
                self.i += 1
            elif is_kw(t, *self._JOIN_MODS):
                mark = self.i
                parts = []
                while self.at_kw(*self._JOIN_MODS):
                    parts.append(self.cur().norm)
                    self.i += 1
                if not self.at_kw("JOIN"):
                    self.i = mark  # não era join — restaura
                    break
                kind = " ".join(parts) + " JOIN"
                self.i += 1
            else:
                break
            right = self.primary(subs, depth)
            if self.accept_kw("ON"):
                refs = self.expr(_EXPR_STOPS)
                subs.extend(refs.subqueries)
            elif self.accept_kw("USING"):
                self.skip_balanced()
            left = SrcJoin(
                left=left, right=right, kind=kind,
                span=self.span(start.start, self.last_end()),
            )
        return left

    def primary(self, subs: list[SelectStmt], depth: int) -> Source:
        t = self.cur()
        if depth + 1 > _MAX_DEPTH:
            raise ParseError("nesting too deep", t)
        start = self.cur()
        if self.at_punct("("):
            return self._primary_paren(subs, depth, start)
        if self.at_kw("LATERAL", "PIVOT"):
            what = "lateral-view" if t.norm == "LATERAL" else "pivot"
            self._skip_source_tail()
            return SrcUnsupported(
                what=what, written=t.norm,
                span=self.span(start.start, self.last_end()),
            )
        if self.at_kw("UNNEST"):
            return self._primary_unnest(t, start)
        if self.at_kw("VALUES"):
            return self._primary_values(start)
        if is_word(t):
            return self._primary_word(t, start)
        if t.kind == "dynamic":
            self.i += 1
            return SrcUnsupported(
                what="dynamic", written=t.text,
                span=self.span(start.start, self.last_end()),
            )
        raise ParseError("expected table source", t)

    def _primary_paren(
        self, subs: list[SelectStmt], depth: int, start: Token
    ) -> Source:
        """``( query )`` subquery ou ``( a JOIN b )`` grupo parentizado."""
        if is_kw(self.peek(), "SELECT", "WITH"):
            self.i += 1
            q = self.query()
            self.expect_punct(")")
            alias, cols = self.opt_alias(cols=True)
            return SrcSub(
                query=q, alias=alias, col_aliases=cols,
                span=self.span(start.start, self.last_end()),
            )
        # fonte agrupada: ( a JOIN b … ) [alias]
        self.i += 1
        inner = self.join_chain(subs, depth + 1)
        self.expect_punct(")")
        alias, _ = self.opt_alias()
        return SrcGroup(
            inner=inner, alias=alias,
            span=self.span(start.start, self.last_end()),
        )

    def _primary_unnest(self, t: Token, start: Token) -> Source:
        """``UNNEST(arr) [WITH ORDINALITY] [AS u(x)]`` — fonte opaca."""
        self.i += 1
        if self.at_punct("("):
            self.skip_balanced()
        self.accept_kw("WITH")
        self.accept_kw("ORDINALITY")
        self.opt_alias()
        return SrcUnsupported(
            what="unnest", written=t.norm,
            span=self.span(start.start, self.last_end()),
        )

    def _primary_values(self, start: Token) -> Source:
        """``FROM VALUES (…), (…)`` — dados literais; linhagem termina."""
        self.i += 1
        while self.at_punct("("):
            self.skip_balanced()
            if not self.accept_punct(","):
                break
        alias, cols = self.opt_alias(cols=True)
        return SrcValues(
            alias=alias, col_aliases=cols,
            span=self.span(start.start, self.last_end()),
        )

    def _primary_word(self, t: Token, start: Token) -> Source:
        """``name[.name]*`` tabela ou ``fn(…)`` função de tabela (opaca)."""
        if self.peek().kind == "punct" and self.peek().text == "(":
            # função de tabela (JSON_TABLE, explode, …) — não expande
            self.i += 1
            self.skip_balanced()
            self.opt_alias()
            return SrcUnsupported(
                what="table-function", written=t.text,
                span=self.span(start.start, self.last_end()),
            )
        name = self.name_chain()
        alias, _ = self.opt_alias()
        return SrcTable(
            name=name, alias=alias,
            span=self.span(start.start, self.last_end()),
        )

    def _skip_source_tail(self) -> None:
        """Consome fonte não-suportada até fronteira (join/vírgula/cláusula)."""
        depth = 0
        while not self.at_eof():
            t = self.cur()
            if t.kind == "punct":
                if t.text in "([":
                    depth += 1
                elif t.text in ")]":
                    if depth == 0:
                        return
                    depth -= 1
                elif (t.text == "," and depth == 0) or (t.text == ";" and depth == 0):
                    return
            if depth == 0 and t.kind == "word" and t.norm in _EXPR_STOPS:
                return
            self.i += 1

    # --- expressões -----------------------------------------------------

    def expr(self, stops: frozenset[str]) -> ExprRefs:
        """Scan de expressão coletando colrefs/stars/subqueries/dynamic.

        Termina em keyword de ``stops`` ou vírgula/``;`` em depth 0, ou
        em ``)`` que não abrimos. CASE/WHEN é balanceado (``case_depth``)
        para não cortar expressões no meio. Keywords não-estruturais são
        operadores/cláusulas internas — consumidas, nunca colrefs.

        ``value_end``: após um valor completo em nível 0 (colref,
        ``)`` fechando o nível, literal), uma palavra solta que não é
        keyword de expressão é início de **alias implícito** — para a
        expressão. Sem isso ``SELECT a b FROM`` engoliria ``b`` como
        colref fantasma.
        """
        s = _ExprScan()
        while True:
            t = self.cur()
            if t.kind == "eof":
                break
            if t.kind == "punct":
                if self._expr_punct(s, t):
                    break
                continue
            if t.kind == "dynamic":
                s.refs.dynamic = True
                self.i += 1
                continue
            if t.kind in ("string", "number", "param"):
                s.recent.clear()
                s.value_end = True  # literal completa um valor
                self.i += 1
                continue
            if t.kind in ("word", "qident"):
                if self._expr_word(s, t, stops):
                    break
                continue
            self.i += 1
        return self._finish_expr(s)

    def _finish_expr(self, s: _ExprScan) -> ExprRefs:
        """Fecha o scan: parênses abertos = erro; lambda-bound filtrado."""
        if s.lvl > 0:
            # EOF com parênses abertos — nunca silencia
            raise ParseError("unbalanced parens", self.cur())
        # lambda-bound: refs coletados com nome ligado no mesmo nível
        # (ou raso) caem — bound por nível sobrevive ao fecho dos parênses
        if any(s.bound.values()):
            s.refs.cols = [
                col for (col, d) in s.tagged
                if not (
                    len(col.parts) == 1
                    and any(
                        col.parts[0] in b
                        for k, b in s.bound.items() if k <= d
                    )
                )
            ]
        return s.refs

    def _expr_punct(self, s: _ExprScan, t: Token) -> bool:
        """Pontuação dentro de ``expr`` — True = expressão terminou."""
        if t.text == "(":
            self._expr_open(s)
            return False
        if t.text == ")":
            if s.lvl == 0:
                return True  # fecho do caller
            s.lvl -= 1
            if s.lvl == 0:
                s.value_end = True  # `) x` — fecho de valor → alias
            self.i += 1
            return False
        if t.text in (",", ";") and s.lvl == 0:
            return True
        if t.text == ";":
            # `;` dentro de parênses não-fechados — malformado
            raise ParseError("unbalanced parens", t)
        if t.text == "*":
            self._expr_star(s, t)
            return False
        if t.text == "->":
            s.bound.setdefault(s.lvl, set()).update(s.recent)
            s.recent.clear()
            s.value_end = False
            self.i += 1
            return False
        if t.text == "::":
            # cast postfixado — o tipo não é colref
            self.i += 1
            self._skip_type()
            s.recent.clear()
            s.value_end = s.lvl == 0
            return False
        # operador/pontuação — divide candidatos a parâmetro de
        # lambda (`,`` em `f(xs, x -> ...)`) e segue a expressão
        s.value_end = False
        s.recent.clear()
        self.i += 1
        return False

    def _expr_open(self, s: _ExprScan) -> None:
        """``(`` dentro de ``expr``: subquery ou nível de profundidade."""
        if is_kw(self.peek(), "SELECT", "WITH"):
            self.i += 1
            q = self.query()
            self.expect_punct(")")
            s.refs.subqueries.append(q)
            s.recent.clear()
            s.value_end = True
            return
        s.lvl += 1
        s.recent.clear()
        s.value_end = False
        self.i += 1

    def _expr_star(self, s: _ExprScan, t: Token) -> None:
        """``*``/``t.*`` — qualificador vem do colref imediatamente anterior."""
        qual: tuple[str, ...] = ()
        prev = self.toks[self.i - 1] if self.i else t
        if (
            prev.kind == "punct" and prev.text == "."
            and s.tagged and s.tagged[-1][1] == s.lvl
        ):
            qual = s.tagged[-1][0].parts
            s.tagged.pop()
            s.refs.cols.pop()
        star = StarRef(qualifier=qual, span=self.span(t.start, t.end))
        s.refs.stars.append(star)
        if s.lvl == 0:
            s.refs.top_star = star
            s.value_end = True
        s.recent.clear()
        self.i += 1

    def _expr_word(
        self, s: _ExprScan, t: Token, stops: frozenset[str]
    ) -> bool:
        """Palavra/qident dentro de ``expr`` — True = expressão terminou."""
        norm = t.norm
        # keywords só atuam como keywords quando não-quotadas
        if t.kind == "word":
            kw = self._expr_keyword(s, t, stops)
            if kw == "end":
                return True
            if kw == "consumed":
                return False
        if s.value_end and s.lvl == 0 and s.case_depth == 0:
            return True  # alias implícito: valor completo + palavra solta
        # cadeia de identificador: part(.part)*
        parts = [t.iname]
        self.i += 1
        while self.at_punct("."):
            nx = self.peek()
            if is_word(nx) or nx.kind == "number":
                parts.append(nx.iname)
                self.i += 2
                continue
            break  # `.*` fica para o ramo de star
        if self.at_punct("(") or self.at_punct("["):
            # chamada de função/construtor — o nome não é colref
            if t.kind == "word" and norm in _SPECIAL_ARG_FNS:
                self._special_fn_field(norm)
            s.recent.clear()
            s.value_end = False
            return False
        col = ColRef(
            parts=tuple(parts),
            span=self.span(t.start, self.last_end()),
        )
        s.refs.cols.append(col)
        s.tagged.append((col, s.lvl))
        # apenas idents simples podem ser parâmetros de lambda
        s.recent.clear()
        if len(parts) == 1:
            s.recent.append(parts[0])
        s.value_end = s.lvl == 0
        return False

    def _expr_keyword(
        self, s: _ExprScan, t: Token, stops: frozenset[str]
    ) -> str:
        """Keyword dentro de ``expr``: ``end``|``consumed``|``ident``.

        ``ident`` = a palavra não atua como keyword aqui — o caller
        segue para a cadeia de identificador (colref/alias).
        """
        norm = t.norm
        if s.lvl == 0 and s.case_depth == 0 and norm in stops:
            return "end"
        case = self._expr_case_kw(s, norm)
        if case is not None:
            return case
        if norm == "INTERVAL":
            # INTERVAL '1' DAY / INTERVAL 1 YEAR TO MONTH
            self.i += 1
            if self.cur().kind in ("string", "number"):
                self.i += 1
            while is_word(self.cur()) and (
                self.cur().norm in _INTERVAL_UNITS
            ):
                self.i += 1
            s.recent.clear()
            s.value_end = s.lvl == 0
            return "consumed"
        if norm in _TYPE_LITERAL_WORDS and self.peek().kind == "string":
            self.i += 2  # DATE '…' — literal, não colref
            s.recent.clear()
            s.value_end = s.lvl == 0
            return "consumed"
        if norm == "AS" and s.lvl > 0:
            # CAST(x AS tipo) — pula a cadeia de tipo
            self.i += 1
            self._skip_type()
            s.recent.clear()
            return "consumed"
        if norm in KEYWORDS_FOR_EXPR:
            # operador/cláusula interna — não é colref
            self.i += 1
            s.recent.clear()
            # literais completam valor; operadores seguem
            s.value_end = norm in _EXPR_LITERALS and s.lvl == 0
            return "consumed"
        return "ident"

    def _expr_case_kw(self, s: _ExprScan, norm: str) -> str | None:
        """CASE/WHEN/THEN/ELSE/END — balanceado dentro de ``expr``.

        ``None`` = não é keyword de CASE — segue o dispatch normal.
        """
        if norm == "CASE":
            s.case_depth += 1
            s.value_end = False
            self.i += 1
            return "consumed"
        if norm == "END" and s.case_depth > 0:
            s.case_depth -= 1
            s.value_end = s.lvl == 0 and s.case_depth == 0
            self.i += 1
            return "consumed"
        if (
            norm in ("WHEN", "THEN", "ELSE")
            and s.case_depth == 0 and s.lvl == 0
        ):
            return "end"
        return None

    def _special_fn_field(self, fn: str) -> None:
        """``EXTRACT(YEAR FROM …)``: após '(', salta campo de extração.

        Só pula a primeira palavra quando ela é field-word conhecida
        (``YEAR``/``BOTH``/``LEADING``…) ou quando ``FROM``/``IN``/``FOR``
        segue num EXTRACT — ``SUBSTRING(x FROM …)`` mantém ``x``.
        """
        if not self.at_punct("("):
            return
        self.i += 1
        t = self.cur()
        if not is_word(t):
            return
        if t.norm in _FN_FIELD_WORDS or (
            fn == "EXTRACT" and is_kw(self.peek(), "FROM", "IN", "FOR")
        ):
            self.i += 1

    def _skip_type(self) -> None:
        """Pula nome de tipo: word* [ (…) ] [ <…> ] (``x::decimal(10,2)``)."""
        while True:
            t = self.cur()
            if is_word(t) or t.kind == "number":
                self.i += 1
                if self.at_punct("."):
                    self.i += 1
                    continue
            if self.at_punct("("):
                self.skip_balanced()
                continue
            if self.at_punct("<"):
                depth = 0
                while not self.at_eof():
                    c = self.cur()
                    if c.kind == "punct" and c.text == "<":
                        depth += 1
                    elif c.kind == "punct" and c.text == ">":
                        depth -= 1
                        if depth == 0:
                            self.i += 1
                            break
                    self.i += 1
                continue
            return

    # --- DML -----------------------------------------------------------

    def insert_stmt(self, ctes: list[Cte], start: Token) -> Statement:
        self.expect_kw("INSERT")
        overwrite = self.accept_kw("OVERWRITE")
        self.accept_kw("INTO")
        self.accept_kw("TABLE")
        target = self.name_chain(loose=True)
        st = SInsert(
            kind="insert",
            span=self.span(start.start, self.last_end()),
            target=target, overwrite=overwrite,
        )
        # (cols) opcional — '(' que NÃO abre select/values
        if self.at_punct("(") and not is_kw(self.peek(), "SELECT", "WITH", "VALUES"):
            mark = self.i
            try:
                st.columns = self._col_list()
            except ParseError:
                self.i = mark  # talvez seja query parentizada — tenta adiante
        if self.accept_kw("PARTITION"):
            if self.at_punct("("):
                self.skip_balanced()
            self.accept_kw("IF")
            self.accept_kw("NOT")
            self.accept_kw("EXISTS")
        if self.at_kw("SELECT", "WITH") or (
            self.at_punct("(") and is_kw(self.peek(), "SELECT", "WITH")
        ):
            q = self.query()
            if ctes:
                q.ctes = ctes + q.ctes
            st.query = q
        elif self.accept_kw("VALUES"):
            while self.at_punct("("):
                st.value_rows.append(self._values_row())
                if not self.accept_punct(","):
                    break
        else:
            raise ParseError("INSERT without source query", self.cur())
        st.span = self.span(start.start, self.last_end())
        return st

    def _values_row(self) -> list[ExprRefs]:
        """``( expr, … )`` de VALUES — ExprRefs por posição."""
        self.expect_punct("(")
        row: list[ExprRefs] = []
        if self.at_punct(")"):
            self.i += 1
            return row
        while True:
            row.append(self.expr(_EXPR_STOPS))
            if self.accept_punct(","):
                continue
            self.expect_punct(")")
            return row

    def update_stmt(self, ctes: list[Cte], start: Token) -> Statement:
        self.expect_kw("UPDATE")
        target = self.name_chain(loose=True)
        alias, _ = self.opt_alias()
        self.expect_kw("SET")
        sets: list[tuple[str, ExprRefs]] = []
        while True:
            col = self.name_chain(loose=True)
            self.expect_punct("=")
            refs = self.expr(_EXPR_STOPS)
            sets.append((col.parts[-1], refs))
            if self.accept_punct(","):
                continue
            break
        st = SUpdate(
            kind="update",
            span=self.span(start.start, self.last_end()),
            target=target, target_alias=alias, sets=sets, ctes=ctes,
        )
        if self.accept_kw("FROM"):
            st.sources = self.source_list(st.tail_subqueries)
        if self.accept_kw("WHERE"):
            refs = self.expr(_EXPR_STOPS)
            st.tail_subqueries.extend(refs.subqueries)
        st.span = self.span(start.start, self.last_end())
        return st

    def delete_stmt(self, ctes: list[Cte], start: Token) -> Statement:
        self.expect_kw("DELETE")
        self.accept_kw("FROM")
        target = self.name_chain(loose=True)
        self.opt_alias()
        st = SDelete(
            kind="delete",
            span=self.span(start.start, self.last_end()),
            target=target, ctes=ctes,
        )
        if self.accept_kw("USING"):
            st.sources = self.source_list(st.tail_subqueries)
        if self.accept_kw("WHERE"):
            refs = self.expr(_EXPR_STOPS)
            st.tail_subqueries.extend(refs.subqueries)
        st.span = self.span(start.start, self.last_end())
        return st

    def merge_stmt(self, ctes: list[Cte], start: Token) -> Statement:
        self.expect_kw("MERGE")
        self.accept_kw("INTO")
        target = self.name_chain(loose=True)
        alias, _ = self.opt_alias()
        self.expect_kw("USING")
        src_subs: list[SelectStmt] = []
        source = self.primary(src_subs, 0)
        self.expect_kw("ON")
        refs = self.expr(_EXPR_STOPS)
        st = SMerge(
            kind="merge",
            span=self.span(start.start, self.last_end()),
            target=target, target_alias=alias, source=source, ctes=ctes,
            tail_subqueries=[*src_subs, *refs.subqueries],
        )
        while self.at_kw("WHEN"):
            self._merge_when(st)
        st.span = self.span(start.start, self.last_end())
        return st

    def _merge_when(self, st: SMerge) -> None:
        """``WHEN [NOT] MATCHED [BY …] [AND …] THEN <ação>``."""
        self.i += 1
        self.accept_kw("NOT")
        if not self.accept_kw("MATCHED"):
            raise ParseError("expected MATCHED in WHEN clause", self.cur())
        if self.accept_kw("BY"):
            self.accept_kw("TARGET", "SOURCE")
        if self.accept_kw("AND"):
            refs = self.expr(_EXPR_STOPS)
            st.tail_subqueries.extend(refs.subqueries)
        self.expect_kw("THEN")
        self._merge_action(st)

    def _merge_action(self, st: SMerge) -> None:
        """Ação do WHEN: ``UPDATE SET`` / ``DELETE`` / ``INSERT VALUES``."""
        t = self.cur()
        if self.accept_kw("UPDATE"):
            self.expect_kw("SET")
            while True:
                col = self.name_chain(loose=True)
                self.expect_punct("=")
                refs = self.expr(_EXPR_STOPS)
                st.sets.append((col.parts[-1], refs))
                if self.accept_punct(","):
                    continue
                break
            return
        if self.accept_kw("DELETE"):
            return
        if self.accept_kw("INSERT"):
            if self.at_punct("(") and not is_kw(
                self.peek(), "SELECT", "WITH", "VALUES"
            ):
                st.insert_cols = self._col_list()
            self.expect_kw("VALUES")
            st.insert_exprs = self._values_row()
            return
        if self.accept_kw("DO"):
            self.accept_kw("NOTHING")
            return
        raise ParseError("unsupported WHEN clause", t)

    # --- DDL --------------------------------------------------------------

    def create_stmt(self, start: Token) -> Statement:
        self.expect_kw("CREATE")
        or_replace = False
        temporary = False
        external = False
        materialized = False
        while True:
            if self.accept_kw("OR"):
                self.expect_kw("REPLACE")
                or_replace = True
            elif self.accept_kw("TEMP", "TEMPORARY", "GLOBAL", "TRANSIENT"):
                temporary = True
            elif self.accept_kw("EXTERNAL"):
                external = True
            elif self.accept_kw("MATERIALIZED"):
                materialized = True
            elif self.accept_kw("UNIQUE"):
                pass
            else:
                break
        if self.accept_kw("TABLE"):
            return self.create_table(
                start, or_replace=or_replace, temporary=temporary,
                external=external,
            )
        if self.accept_kw("VIEW"):
            return self.create_view(
                start, or_replace=or_replace, temporary=temporary,
                materialized=materialized,
            )
        t = self.cur()
        if is_kw(t, *_CREATE_SUB):
            st = SUnsupported(
                kind="unsupported", span=self.span(start.start, t.end),
                why=f"create-{t.norm.lower()}", head=t.norm,
            )
            self.skip_to_boundary()
            return st
        raise ParseError("unsupported CREATE target", t)

    def create_table(
        self, start: Token, *, or_replace: bool, temporary: bool, external: bool
    ) -> Statement:
        self.accept_kw("IF")
        self.accept_kw("NOT")
        self.accept_kw("EXISTS")
        name = self.name_chain(loose=True)
        st = SCreateTable(
            kind="create-table",
            span=self.span(start.start, self.last_end()),
            name=name, or_replace=or_replace, temporary=temporary,
            external=external,
        )
        # primeira lista de parênteses logo após o nome = definição de colunas
        if self.at_punct("(") and not is_kw(self.peek(), "SELECT", "WITH", "VALUES"):
            mark = self.i
            try:
                st.columns = self._coldefs()
            except ParseError:
                self.i = mark
                self.skip_balanced()
        # opções de tabela até AS (CTAS) ou fim do statement
        prev_kw = ""
        while not self.at_eof() and not self.at_punct(";"):
            t = self.cur()
            if self.at_kw("AS") and prev_kw != "STORED":
                self.i += 1
                st.query = self.query()
                break
            if self.at_kw("LIKE") and st.like is None:
                self.i += 1
                st.like = self.name_chain(loose=True)
                prev_kw = "LIKE"
                continue
            if t.kind == "punct" and t.text == "(":
                self.skip_balanced()
                continue
            if t.kind == "word":
                prev_kw = t.norm
            self.i += 1
        # resíduo de opções após o SELECT (WITH NO DATA, ENGINE, …)
        while not self.at_eof() and not self.at_punct(";"):
            self.i += 1
        st.span = self.span(start.start, self.last_end())
        return st

    def _coldefs(self) -> list[tuple[str, str]]:
        """``( coldef|constraint, … )`` — coldef = nome + texto de tipo."""
        self.expect_punct("(")
        cols: list[tuple[str, str]] = []
        if self.at_punct(")"):
            self.i += 1
            return cols
        while True:
            t = self.cur()
            if is_kw(t, *_CONSTRAINT_HEAD) or is_kw(t, "LIKE"):
                self._skip_table_constraint()
            else:
                cols.append(self._coldef_item())
            if self.accept_punct(","):
                continue
            self.expect_punct(")")
            return cols

    def _skip_table_constraint(self) -> None:
        """Constraint de tabela — consome o item inteiro até ``,``/``)``."""
        while (
            not self.at_eof()
            and not self.at_punct(",")
            and not self.at_punct(")")
        ):
            if self.at_punct("("):
                self.skip_balanced()
                continue
            self.i += 1

    def _coldef_item(self) -> tuple[str, str]:
        """``nome tipo[…]`` — o tipo é preservado como texto bruto."""
        t = self.cur()
        if not is_word(t):
            raise ParseError("expected column definition", t)
        cname = t.iname
        self.i += 1
        type_parts: list[str] = []
        depth = 0
        while not self.at_eof():
            c = self.cur()
            if c.kind == "punct" and c.text == "(":
                depth += 1
            elif c.kind == "punct" and c.text == ")":
                if depth == 0:
                    break
                depth -= 1
            elif c.kind == "punct" and c.text == "," and depth == 0:
                break
            type_parts.append(c.text)
            self.i += 1
        return cname, " ".join(type_parts)

    def create_view(
        self, start: Token, *, or_replace: bool, temporary: bool, materialized: bool
    ) -> Statement:
        self.accept_kw("IF")
        self.accept_kw("NOT")
        self.accept_kw("EXISTS")
        name = self.name_chain(loose=True)
        st = SCreateView(
            kind="create-view",
            span=self.span(start.start, self.last_end()),
            name=name, or_replace=or_replace, temporary=temporary,
            materialized=materialized,
        )
        if self.at_punct("(") and not is_kw(self.peek(), "SELECT", "WITH"):
            st.col_aliases = self._col_list()
        # opções até AS — COMMENT/TBLPROPERTIES são comuns em views
        while not self.at_eof() and not self.at_punct(";"):
            if self.at_kw("AS"):
                self.i += 1
                st.query = self.query()
                break
            if self.at_punct("("):
                self.skip_balanced()
                continue
            self.i += 1
        if st.query is None:
            raise ParseError("CREATE VIEW without AS", self.cur())
        # resíduo de opções após o SELECT (WITH CHECK OPTION, …)
        while not self.at_eof() and not self.at_punct(";"):
            self.i += 1
        st.span = self.span(start.start, self.last_end())
        return st

    def prepare_stmt(self, start: Token) -> Statement:
        self.expect_kw("PREPARE")
        t = self.cur()
        if not is_word(t):
            raise ParseError("expected prepared statement name", t)
        name = t.iname
        self.i += 1
        self.expect_kw("FROM")
        if self.at_kw("SELECT", "WITH"):
            q = self.query()
            return SSelect(
                kind="select",
                span=self.span(start.start, self.last_end()),
                query=q, prepared_name=name,
            )
        raise ParseError("PREPARE with non-SELECT body", self.cur())


# Keywords aceitas DENTRO de expressões (operadores, cláusulas internas,
# keywords de função). Separado de _EXPR_STOPS: estas não terminam o
# scan — apenas são consumidas sem virar colrefs.
KEYWORDS_FOR_EXPR: frozenset[str] = frozenset(
    {
        "AND", "OR", "NOT", "IN", "IS", "NULL", "LIKE", "ILIKE", "RLIKE",
        "REGEXP", "BETWEEN", "EXISTS", "CAST", "TRY_CAST", "AS",
        "DISTINCT", "ALL", "CASE", "WHEN", "THEN", "ELSE", "END",
        "OVER", "FILTER", "WITHIN", "PARTITION", "ORDER", "BY", "ASC",
        "DESC", "NULLS", "FIRST", "LAST", "GROUPING", "ROLLUP", "CUBE",
        "SETS", "UNNEST", "ORDINALITY", "FROM", "FOR", "SEPARATOR",
        "RESPECT", "IGNORE", "RANGE", "UNBOUNDED", "PRECEDING",
        "FOLLOWING", "CURRENT", "TIES", "OTHERS", "EXCLUDE", "GROUPS",
        "ARRAY", "MAP", "STRUCT", "ROW", "ESCAPE", "SIMILAR",
    }
)

# Keywords que completam um valor (literais de SQL) — após elas, uma
# palavra solta é alias implícito, não continuação da expressão.
_EXPR_LITERALS: frozenset[str] = frozenset(
    {
        "NULL", "TRUE", "FALSE", "DEFAULT", "CURRENT_DATE",
        "CURRENT_TIME", "CURRENT_TIMESTAMP", "CURRENT_USER",
        "SESSION_USER", "LOCALTIME", "LOCALTIMESTAMP", "USER",
    }
)
