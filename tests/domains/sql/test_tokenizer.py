"""Tokenizer stdlib — tokens, problemas e marcadores dinâmicos."""

from __future__ import annotations

from code_atlas.domains.sql.tokens import is_kw, is_name, tokenize


def kinds(toks):
    return [t.kind for t in toks if t.kind != "eof"]


def test_basic_statement_tokens() -> None:
    toks, problems = tokenize("SELECT a, b.c FROM t;")
    assert problems == []
    assert kinds(toks) == ["word", "word", "punct", "word", "punct",
                           "word", "word", "word", "punct"]
    assert toks[0].norm == "SELECT"
    assert toks[3].text == "b" and toks[5].text == "c"


def test_line_and_block_comments_are_consumed() -> None:
    toks, problems = tokenize(
        "SELECT -- comentário\na /* bloco /* aninhado */ , b FROM t"
    )
    assert problems == []
    words = [t.norm for t in toks if t.kind == "word"]
    assert words == ["SELECT", "A", "B", "FROM", "T"]


def test_unterminated_block_comment_is_flagged() -> None:
    _toks, problems = tokenize("SELECT a /* nunca fecha")
    assert problems == ["unterminated-comment"]


def test_strings_and_escaped_quotes() -> None:
    toks, _ = tokenize("SELECT 'it''s', E'x', N'y' FROM t")
    strings = [t.text for t in toks if t.kind == "string"]
    assert strings == ["'it''s'", "E'x'", "N'y'"]


def test_unterminated_string_is_flagged() -> None:
    _toks, problems = tokenize("SELECT 'abc")
    assert problems == ["unterminated-string"]


def test_quoted_identifiers_preserve_case() -> None:
    toks, problems = tokenize('SELECT "Weird Col", `CaseT` FROM t')
    assert problems == []
    q = [t for t in toks if t.kind == "qident"]
    assert q[0].iname == "Weird Col" and q[0].norm == "Weird Col"
    assert q[1].iname == "CaseT"


def test_doubled_quote_inside_quoted_ident() -> None:
    toks, problems = tokenize('SELECT "a""b" FROM t')
    assert problems == []
    q = [t for t in toks if t.kind == "qident"]
    assert q[0].iname == 'a"b'


def test_dynamic_markers() -> None:
    for marker in ("${t}", "{{ ref('m') }}", "{% if x %}", "@{p}", "<% s %>"):
        toks, _ = tokenize(f"SELECT a FROM {marker}")
        dyn = [t for t in toks if t.kind == "dynamic"]
        assert dyn, marker


def test_minus_is_not_comment_without_double_dash() -> None:
    toks, problems = tokenize("SELECT a - 1 FROM t")
    assert problems == []
    assert "punct" in kinds(toks)


def test_params_are_not_columns() -> None:
    toks, _ = tokenize("SELECT a FROM t WHERE x = ? AND y = :y AND z = $1")
    assert [t.kind for t in toks].count("param") == 3


def test_keyword_helpers() -> None:
    toks, _ = tokenize('SELECT "SELECT" FROM t')
    assert is_kw(toks[0], "SELECT")
    assert not is_kw(toks[1], "SELECT")  # quotado não é keyword
    assert not is_name(toks[0])          # keyword não é nome
    assert is_name(toks[1])              # qident é nome
