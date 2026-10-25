"""yamlmini — subconjunto YAML honesto (lf-0116).

Cobre as formas que workflows GitHub Actions usam de verdade e prova
que construções fora do subset viram diagnósticos — nunca valores
fabricados.
"""

from __future__ import annotations

import pytest

from code_atlas.domains.cicd.yamlmini import YNode, parse


def _root(text: str) -> YNode:
    r = parse(text)
    assert r.root is not None, [d.message for d in r.diagnostics]
    return r.root


def test_map_seq_scalar_basics() -> None:
    r = parse("name: CI\non: push\n")
    assert not r.diagnostics
    root = r.root
    assert root is not None and root.kind == "map"
    assert root.get("name").value == "CI"  # type: ignore[union-attr]
    # invariante crítica: 'on' permanece string — nunca vira True
    assert "on" in tuple(root.keys())


def test_on_never_becomes_boolean() -> None:
    root = _root("on: push\non2: yes\n")
    assert root.get("on").value == "push"  # type: ignore[union-attr]
    assert root.get("on2").value == "yes"  # type: ignore[union-attr]


def test_nested_map_and_seq_of_maps() -> None:
    root = _root(
        "jobs:\n"
        "  build:\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - name: Run\n"
        "        run: echo hi\n"
    )
    steps = root.get("jobs").get("build").get("steps")  # type: ignore[union-attr]
    assert steps.kind == "seq"
    first = steps.items[0]
    assert first.get("uses").value == "actions/checkout@v4"  # type: ignore[union-attr]
    second = steps.items[1]
    assert second.get("name").value == "Run"  # type: ignore[union-attr]
    assert second.get("run").value == "echo hi"  # type: ignore[union-attr]


def test_same_indent_seq_under_key() -> None:
    """YAML permite 'key:' seguida de '- ' na MESMA coluna."""
    root = _root("needs:\n- lint\n- test\n")
    assert root.get("needs").scalar_list() == ("lint", "test")  # type: ignore[union-attr]


def test_flow_seq_and_scalar_forms() -> None:
    root = _root("on: [push, pull_request]\nbranches: {main: 1}\n")
    assert root.get("on").scalar_list() == ("push", "pull_request")  # type: ignore[union-attr]
    fm = root.get("branches")
    assert fm.kind == "map" and fm.get("main").value == "1"  # type: ignore[union-attr]


def test_flow_map_nested() -> None:
    root = _root("with: {name: dist, path: [a, b]}\n")
    w = root.get("with")
    assert w.get("name").value == "dist"  # type: ignore[union-attr]
    assert w.get("path").scalar_list() == ("a", "b")  # type: ignore[union-attr]


def test_literal_block_scalar_keeps_newlines() -> None:
    root = _root(
        "run: |\n"
        "  echo one\n"
        "  pytest -q\n"
        "next: ok\n"
    )
    assert root.get("run").value == "echo one\npytest -q\n"  # type: ignore[union-attr]
    assert root.get("next").value == "ok"  # type: ignore[union-attr]


def test_literal_block_chomp_strip() -> None:
    root = _root("run: |-\n  echo hi\n\n  echo bye\n")
    assert root.get("run").value == "echo hi\n\necho bye"  # type: ignore[union-attr]


def test_folded_scalar() -> None:
    root = _root("run: >\n  echo one\n  echo two\n")
    assert "echo one echo two" in root.get("run").value  # type: ignore[union-attr]


def test_comments_and_doc_markers() -> None:
    root = _root(
        "# leading comment\n"
        "---\n"
        "name: CI  # trailing\n"
        "x: 1 # comment\n"
        "...\n"
    )
    assert root.get("name").value == "CI"  # type: ignore[union-attr]
    assert root.get("x").value == "1"  # type: ignore[union-attr]


def test_quoted_scalars() -> None:
    root = _root(
        "a: 'it''s'\n"
        'b: "say \\"hi\\""\n'
        'c: "http://x:8080"\n'
    )
    assert root.get("a").value == "it's"  # type: ignore[union-attr]
    assert root.get("b").value == 'say "hi"'  # type: ignore[union-attr]
    assert root.get("c").value == "http://x:8080"  # type: ignore[union-attr]


def test_url_colon_is_not_key_separator() -> None:
    root = _root("run: curl http://example.com:8080/x\n")
    assert root.get("run").value == "curl http://example.com:8080/x"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("x: &anchor val\n", "anchor-unsupported"),
        ("x: *alias\n", "alias-unsupported"),
        ("x:\n  <<: *base\n", "merge-key-unsupported"),
        ("x: !tag val\n", "tag-unsupported"),
        ("a: 1\n\tb: 2\n", "tab-indent"),
        ("a: 1\n---\nb: 2\n", "multi-document-unsupported"),
        ("a: 'unclosed\n", "unterminated-quote"),
        ("a: [1, 2\n", "flow-unclosed"),
        ("a:\n  b: 1\n   c: 2\n", "unexpected-indent"),
        ("a: 1\na: 2\n", "duplicate-key"),
    ],
)
def test_unsupported_constructs_are_diagnostics_not_fabricated(
    text: str, code: str
) -> None:
    r = parse(text)
    assert any(d.code == code for d in r.diagnostics), (
        f"expected {code} in {[d.code for d in r.diagnostics]}"
    )


def test_anchor_value_kept_alias_is_null() -> None:
    """Anchor nomeia mas não muda o valor → mantido; alias não resolve → null."""
    root = _root("a: &x 1\nb: *x\n")
    assert root.get("a").value == "1"  # type: ignore[union-attr]
    assert root.get("b").kind == "null"  # type: ignore[union-attr]


def test_empty_document() -> None:
    assert parse("").root is None
    assert parse("# only comments\n").root is None


def test_spans_are_real_positions() -> None:
    text = "name: CI\non: push\n"
    r = parse(text)
    root = r.root
    assert root is not None
    on = root.get("on")
    assert on is not None
    assert on.span.start_line == 2
    # bytes offsets apontam para a linha real do valor
    line2 = text.split("\n")[1]
    assert on.span.start_byte == len("name: CI\n") + line2.index("push")


def test_scalar_list_uniform() -> None:
    """needs: x e needs: [x] produzem a mesma lista."""
    a = _root("needs: lint\n").get("needs")
    b = _root("needs: [lint]\n").get("needs")
    assert a.scalar_list() == b.scalar_list() == ("lint",)  # type: ignore[union-attr]


def test_bad_lines_recover_siblings() -> None:
    r = parse("a:\n  good: 1\n  garbage line here\n  also_good: 2\n")
    keys = r.root.get("a").keys()  # type: ignore[union-attr]
    assert "good" in keys and "also_good" in keys
    assert any(d.code == "expected-key" for d in r.diagnostics)
