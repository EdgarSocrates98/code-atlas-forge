"""``yaml_subset`` — parser YAML delimitado do adapter api (lf-0114)."""

from __future__ import annotations

from code_atlas.domains.api.yaml_subset import parse_yaml_subset


def _codes(doc) -> list[str]:
    return [i.code for i in doc.issues]


def test_basic_mapping_and_scalars() -> None:
    doc = parse_yaml_subset(
        "a: 1\nb: 2.5\nc: true\nd: null\ne: hello world\nf: 'quoted'\n"
    )
    assert doc.issues == ()
    assert doc.value == {
        "a": 1, "b": 2.5, "c": True, "d": None,
        "e": "hello world", "f": "quoted",
    }


def test_nested_map_and_key_lines() -> None:
    doc = parse_yaml_subset(
        "top:\n  mid:\n    leaf: x\nother: y\n"
    )
    assert doc.value == {"top": {"mid": {"leaf": "x"}}, "other": "y"}
    assert doc.key_lines[("top",)] == 1
    assert doc.key_lines[("top", "mid")] == 2
    assert doc.key_lines[("top", "mid", "leaf")] == 3


def test_sequences_scalar_and_inline_maps() -> None:
    doc = parse_yaml_subset(
        "servers:\n  - url: https://a/v1\n    description: first\n"
        "  - url: https://b/v2\nlist:\n  - one\n  - two\n"
    )
    assert doc.value == {
        "servers": [
            {"url": "https://a/v1", "description": "first"},
            {"url": "https://b/v2"},
        ],
        "list": ["one", "two"],
    }
    assert doc.key_lines[("servers", "0", "url")] == 2
    assert doc.key_lines[("servers", "1", "url")] == 4


def test_flow_values() -> None:
    doc = parse_yaml_subset(
        "tags: [pets, listing]\nobj: {a: 1, b: [x, y]}\nempty: []\n"
    )
    assert doc.value == {
        "tags": ["pets", "listing"],
        "obj": {"a": 1, "b": ["x", "y"]},
        "empty": [],
    }


def test_comments_and_doc_markers() -> None:
    doc = parse_yaml_subset(
        "---\n# header comment\na: 1  # trailing\n\nb: \"# not comment\"\n...\n"
    )
    assert doc.value == {"a": 1, "b": "# not comment"}


def test_multi_document_warns_and_uses_first() -> None:
    doc = parse_yaml_subset("a: 1\n---\nb: 2\n")
    assert doc.value == {"a": 1}
    assert "multi-document" in _codes(doc)


def test_block_scalars() -> None:
    doc = parse_yaml_subset(
        "lit: |\n  line one\n  line two\nfold: >\n  part a\n  part b\n"
    )
    v = doc.value
    assert isinstance(v, dict)
    assert v["lit"] == "line one\nline two\n"
    assert v["fold"] == "part a part b\n"


def test_anchors_aliases_merge_keys_are_diagnostics() -> None:
    doc = parse_yaml_subset(
        "defaults: &d\n  x: 1\nuse:\n  <<: *d\n"
    )
    codes = _codes(doc)
    assert "yaml-anchor-unsupported" in codes
    assert "yaml-merge-key-unsupported" in codes
    assert "yaml-alias-unsupported" in codes


def test_duplicate_keys_warn_first_wins() -> None:
    doc = parse_yaml_subset("a: 1\na: 2\n")
    assert doc.value == {"a": 1}
    assert "duplicate-key" in _codes(doc)


def test_quoted_keys_and_paths() -> None:
    doc = parse_yaml_subset(
        'paths:\n  /pets/{petId}:\n    get: 1\n  "/weird:key": 2\n'
    )
    v = doc.value
    assert isinstance(v, dict)
    assert v["paths"] == {"/pets/{petId}": {"get": 1}, "/weird:key": 2}
    assert doc.key_lines[("paths", "/pets/{petId}", "get")] == 3


def test_unterminated_flow_is_error_not_crash() -> None:
    doc = parse_yaml_subset("a: [1, 2\n")
    assert "flow-parse-error" in _codes(doc)


def test_tab_indent_is_error_not_crash() -> None:
    doc = parse_yaml_subset("a:\n\tb: 1\n")
    assert "tab-indent" in _codes(doc)


def test_numeric_keys_normalized_to_str() -> None:
    doc = parse_yaml_subset("responses:\n  200:\n    x: 1\n  '404':\n    y: 2\n")
    v = doc.value
    assert isinstance(v, dict)
    assert set(v["responses"]) == {"200", "404"}


def test_version_like_scalars_stay_strings() -> None:
    doc = parse_yaml_subset("openapi: 3.0.0\nsemver: 1.2\n")
    v = doc.value
    assert isinstance(v, dict)
    assert v["openapi"] == "3.0.0"
    assert v["semver"] == 1.2
