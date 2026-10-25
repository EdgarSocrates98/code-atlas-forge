"""Tokenizer SQL em stdlib puro (lf-0111, Cycle 7 Phase 3).

Zero dependências: nenhum ``sqlparse``/``sqlglot`` — o tokenizer emite uma
sequência plana de ``Token`` que o parser descendente consome. Posições são
**offsets de caractere** sobre o texto decodificado; a conversão para bytes
(evidence spans) acontece em ``parser._Src``.

Decisões de honestidade:

- ``{{ }}``, ``{% %}``, ``${ }``, ``@{ }`` e ``<% %>`` viram token
  ``dynamic`` — marcadores de template/macro que impedem análise estática
  honesta (dbt/Jinja, Spark ``${var}``, Athena ``@var``);
- comentários são consumidos (não emitem tokens) mas comentário de bloco
  não-terminado vira flag — conteúdo perdido é diagnosticado, nunca
  silenciado;
- ``-`` vs ``--``: comentário de linha só começa com dois hífens;
- identificadores entre ``"..."`` e `` `...` `` são ``qident`` —
  capitalização preservada (política de case do adapter, ADR-0010);
- palavras NÃO-reservadas comuns em esquemas reais (``date``, ``key``,
  ``value``, ``type``, ``user``, ``year``…) ficam fora de ``KEYWORDS``
  para não destruir colrefs legítimos — type-literals como
  ``DATE '2020'`` são reconhecidos por lookahead no scanner de
  expressão, não por pertencimento léxico.
"""

from __future__ import annotations

from dataclasses import dataclass

KEYWORDS: frozenset[str] = frozenset(
    {
        # consulta / cláusulas
        "SELECT", "FROM", "WHERE", "GROUP", "BY", "HAVING", "QUALIFY",
        "ORDER", "LIMIT", "OFFSET", "FETCH", "NEXT", "ROWS", "ONLY",
        "FIRST", "TOP", "WINDOW",
        # combinadores e joins
        "UNION", "INTERSECT", "EXCEPT", "ALL", "DISTINCT", "AS",
        "JOIN", "INNER", "LEFT", "RIGHT", "FULL", "OUTER", "CROSS",
        "NATURAL", "SEMI", "ANTI", "ON", "USING",
        # CTE
        "WITH", "RECURSIVE", "MATERIALIZED",
        # expressão
        "AND", "OR", "NOT", "IN", "IS", "NULL", "NULLS", "LIKE", "ILIKE",
        "RLIKE", "REGEXP", "BETWEEN", "EXISTS", "CASE", "WHEN", "THEN",
        "ELSE", "END", "ASC", "DESC",
        # DML
        "INSERT", "INTO", "OVERWRITE", "VALUES", "UPDATE", "SET",
        "DELETE", "MERGE", "MATCHED",
        # DDL
        "CREATE", "REPLACE", "TEMP", "TEMPORARY", "GLOBAL", "EXTERNAL",
        "TRANSIENT", "TABLE", "VIEW", "SCHEMA", "DATABASE", "USE",
        "DROP", "ALTER", "TRUNCATE", "RENAME", "TO", "ADD", "COLUMN",
        "COLUMNS", "CONSTRAINT", "PRIMARY", "FOREIGN", "REFERENCES",
        "UNIQUE", "CHECK", "DEFAULT", "IF",
        # opções de tabela (Athena/Spark/Hive)
        "PARTITION", "PARTITIONED", "CLUSTERED", "SORTED", "DISTRIBUTED",
        "SKEWED", "STORED", "LOCATION", "TBLPROPERTIES", "OPTIONS",
        "COMMENT", "ROW", "FORMAT", "SERDE", "INPUTFORMAT", "OUTPUTFORMAT",
        "DELIMITED", "FIELDS", "LINES", "COLLECTION", "MAP", "TERMINATED",
        "ESCAPED", "DEFINED", "BUCKET", "BUCKETS", "ENGINE", "TABLESPACE",
        "CHARSET", "COLLATE", "PROPERTIES",
        # extensões de fonte de dados
        "LATERAL", "UNNEST", "ORDINALITY", "PIVOT", "TABLESAMPLE",
        "DISTRIBUTE", "CLUSTER", "SORT",
        # funções analíticas/agregação com sintaxe especial
        "OVER", "FILTER", "WITHIN", "GROUPING", "SETS", "ROLLUP", "CUBE",
        # comandos não-SELECT (parseados como unsupported)
        "EXPLAIN", "DESCRIBE", "SHOW", "ANALYZE", "MSCK",
        "REPAIR", "CACHE", "UNCACHE", "REFRESH", "GRANT", "REVOKE",
        "TRANSACTION", "COMMIT", "ROLLBACK", "BEGIN", "START", "RESET",
        "EXEC", "EXECUTE", "CALL", "PREPARE", "DEALLOCATE", "IMMEDIATE",
        "VACUUM", "OPTIMIZE", "LOAD", "DATA", "LOCAL", "INPATH",
        "DIRECTORY", "EXPORT", "IMPORT", "FUNCTION", "PROCEDURE",
        "INDEX", "TRIGGER", "SEQUENCE", "JAR", "FILE", "ROLE",
    }
)

# Palavras que parecem tipos/literais mas NÃO entram em KEYWORDS porque
# são nomes de coluna comuns em datasets reais (``date``, ``key``,
# ``value``, ``user``, ``type``, ``year``…). O scanner de expressão
# reconhece ``DATE '…'``/``TIMESTAMP '…'`` por lookahead, não por
# keyword-membership — decisão documentada para não destruir colrefs.
_TYPE_LITERAL_WORDS: frozenset[str] = frozenset(
    {"DATE", "TIME", "TIMESTAMP", "DATETIME", "INTERVAL"}
)

# Palavras-campo dentro de funções especiais (EXTRACT/TRIM) que não são
# colrefs: ``EXTRACT(YEAR FROM d)``, ``TRIM(BOTH ' ' FROM c)``.
_SPECIAL_ARG_FNS: frozenset[str] = frozenset(
    {"EXTRACT", "TRIM", "SUBSTRING", "POSITION", "OVERLAY"}
)

_MULTI_PUNCT: tuple[str, ...] = ("<=", ">=", "<>", "!=", "||", "::", "->", "=>")

_DYNAMIC_OPEN: dict[str, str] = {
    "{{": "}}", "{%": "%}", "${": "}", "@{": "}", "<%": "%>",
}

_STRING_PREFIXES = frozenset("eExXbBnNrR")


@dataclass(frozen=True)
class Token:
    """Unidade léxica com offsets de caractere (``start``/``end``)."""

    kind: str   # word|qident|number|string|punct|param|dynamic|eof
    text: str   # fatia crua do texto
    norm: str   # word→UPPER (match de keyword); qident→miolo; demais→text
    iname: str  # identidade: word→lower; qident→miolo com case preservado
    start: int
    end: int


def is_kw(tok: Token, *words: str) -> bool:
    """True se ``tok`` é palavra não-quotada pertencente a ``words``."""
    return tok.kind == "word" and tok.norm in words


def is_word(tok: Token) -> bool:
    """Qualquer identificador (quotado ou não), incluindo keywords."""
    return tok.kind in ("word", "qident")


def is_name(tok: Token) -> bool:
    """Identificador utilizável como nome novo (não-keyword ou quotado)."""
    return tok.kind == "qident" or (tok.kind == "word" and tok.norm not in KEYWORDS)


def _scan_string(text: str, i: int, prefix: bool) -> int:
    """Consome ``'…'`` com escape ``''``; retorna posição após o fecho.

    ``-1`` quando a string não fecha até o fim do texto.
    """
    n = len(text)
    j = i + 1
    if prefix:
        j += 1  # letra de prefixo já incluída pelo caller
    while j < n:
        c = text[j]
        if c == "'":
            if j + 1 < n and text[j + 1] == "'":
                j += 2
                continue
            return j + 1
        j += 1
    return -1


def _scan_comment(
    text: str, i: int, n: int, problems: list[str]
) -> int | None:
    """Comentários são consumidos — ``i`` novo ou ``None``."""
    if text.startswith("--", i):
        j = text.find("\n", i + 2)
        return n if j < 0 else j + 1
    if text.startswith("/*", i):
        j = text.find("*/", i + 2)
        if j < 0:
            problems.append("unterminated-comment")
            return n
        return j + 2
    return None


def _scan_dynamic(
    text: str, i: int, n: int, toks: list[Token]
) -> int | None:
    """Marcadores ``${}``/``{{}}``/``{% %}``/``@{}``/``<% %>``."""
    for op, cl in _DYNAMIC_OPEN.items():
        if not text.startswith(op, i):
            continue
        j = text.find(cl, i + len(op))
        j = n if j < 0 else j + len(cl)
        toks.append(Token("dynamic", text[i:j], text[i:j], text[i:j], i, j))
        return j
    return None


def _scan_string_tok(
    text: str, i: int, n: int, toks: list[Token], problems: list[str]
) -> int | None:
    """``'…'`` com prefixo opcional de 1 letra (``E'x'``, ``N'y'``)."""
    c = text[i]
    if c == "'":
        j = _scan_string(text, i, prefix=False)
    elif c in _STRING_PREFIXES and text.startswith("'", i + 1):
        j = _scan_string(text, i, prefix=True)
    else:
        return None
    if j < 0:
        problems.append("unterminated-string")
        j = n
    toks.append(Token("string", text[i:j], text[i:j], text[i:j], i, j))
    return j


def _scan_qident(
    text: str, i: int, n: int, toks: list[Token], problems: list[str]
) -> int | None:
    """``"…"``/````…```` — case preservado, ``""`` é escape interno."""
    c = text[i]
    if c not in ('"', "`"):
        return None
    j = i + 1
    closed = False
    while j < n:
        if text[j] == c:
            if j + 1 < n and text[j + 1] == c:
                j += 2
                continue
            closed = True
            j += 1
            break
        j += 1
    if not closed:
        problems.append("unterminated-quoted-ident")
    inner = text[i + 1:j - 1] if closed else text[i + 1:j]
    inner = inner.replace(c + c, c)
    toks.append(Token("qident", text[i:j], inner, inner, i, j))
    return j


def _scan_word(
    text: str, i: int, n: int, toks: list[Token]
) -> int | None:
    """Identificadores e keywords — distinção fica para o parser."""
    c = text[i]
    if not (c.isalpha() or c == "_"):
        return None
    j = i + 1
    while j < n and (text[j].isalnum() or text[j] in "_$"):
        j += 1
    word = text[i:j]
    toks.append(Token("word", word, word.upper(), word.lower(), i, j))
    return j


def _scan_number(
    text: str, i: int, n: int, toks: list[Token]
) -> int | None:
    """Literais numéricos — aproximação para pular, nunca avaliados."""
    c = text[i]
    if not (
        c.isdigit() or (c == "." and i + 1 < n and text[i + 1].isdigit())
    ):
        return None
    j = i + 1
    while j < n and (text[j].isalnum() or text[j] in "._+-"):
        # para em '+'/'-' não-exponenciais
        if text[j] in "+-" and text[j - 1] not in "eE":
            break
        j += 1
    toks.append(Token("number", text[i:j], text[i:j], text[i:j], i, j))
    return j


def _scan_param(
    text: str, i: int, n: int, toks: list[Token]
) -> int | None:
    """``?``, ``$1``, ``:name`` — parâmetros, nunca colunas."""
    c = text[i]
    if c == "?" or (c == "$" and i + 1 < n and text[i + 1].isdigit()):
        j = i + 1
        while j < n and text[j].isdigit():
            j += 1
        toks.append(Token("param", text[i:j], text[i:j], text[i:j], i, j))
        return j
    if c == ":" and not text.startswith("::", i) and i + 1 < n and (
        text[i + 1].isalpha() or text[i + 1] == "_"
    ):
        j = i + 1
        while j < n and (text[j].isalnum() or text[j] in "_$"):
            j += 1
        toks.append(Token("param", text[i:j], text[i:j], text[i:j], i, j))
        return j
    return None


def _scan_punct(text: str, i: int, toks: list[Token]) -> int:
    """Pontuação — token de 2 chars quando conhecido (``->``, ``::``…)."""
    two = text[i:i + 2]
    if two in _MULTI_PUNCT:
        toks.append(Token("punct", two, two, two, i, i + 2))
        return i + 2
    toks.append(Token("punct", text[i], text[i], text[i], i, i + 1))
    return i + 1


def tokenize(text: str) -> tuple[list[Token], list[str]]:
    """Tokeniza ``text``; retorna ``(tokens, problemas)``.

    ``problemas`` lista códigos honestos: ``unterminated-comment``,
    ``unterminated-string``, ``unterminated-quoted-ident``. O token EOF
    fecha a sequência sempre.
    """
    toks: list[Token] = []
    problems: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i].isspace():
            i += 1
            continue
        nxt = _scan_comment(text, i, n, problems)
        if nxt is None:
            nxt = _scan_dynamic(text, i, n, toks)
        if nxt is None:
            nxt = _scan_string_tok(text, i, n, toks, problems)
        if nxt is None:
            nxt = _scan_qident(text, i, n, toks, problems)
        if nxt is None:
            nxt = _scan_word(text, i, n, toks)
        if nxt is None:
            nxt = _scan_number(text, i, n, toks)
        if nxt is None:
            nxt = _scan_param(text, i, n, toks)
        i = nxt if nxt is not None else _scan_punct(text, i, toks)
    toks.append(Token("eof", "", "", "", n, n))
    return toks, problems
