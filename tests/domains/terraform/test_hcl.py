"""Parser HCL hand-rolled — estrutura, heredocs, comentários, erros."""

from __future__ import annotations

from code_atlas.domains.terraform.hcl import parse_hcl


def test_parses_resource_block_with_labels_and_attrs() -> None:
    f = parse_hcl(
        'resource "aws_s3_bucket" "data" {\n'
        '  bucket = "x"\n'
        '  count  = 3\n'
        "}\n"
    )
    assert not f.diagnostics
    (blk,) = f.blocks
    assert blk.type == "resource"
    assert blk.labels == ("aws_s3_bucket", "data")
    names = [a.name for a in blk.attributes]
    assert names == ["bucket", "count"]
    assert blk.attribute("bucket").value == '"x"'


def test_nested_blocks_and_multiline_values() -> None:
    f = parse_hcl(
        'resource "aws_glue_job" "j" {\n'
        "  command {\n"
        '    script_location = "s3://b/s.py"\n'
        "  }\n"
        "  tags = {\n"
        '    Env = "dev"\n'
        "  }\n"
        "}\n"
    )
    assert not f.diagnostics
    (blk,) = f.blocks
    assert [c.type for c in blk.children] == ["command"]
    assert blk.children[0].attribute("script_location").value == '"s3://b/s.py"'
    assert blk.attribute("tags") is not None


def test_comments_and_heredoc_are_skipped() -> None:
    f = parse_hcl(
        "# leading comment\n"
        'resource "aws_iam_policy" "p" {  // trailing\n'
        "  policy = <<EOF\n"
        '{"Version": "2012-10-17", "Statement": []}\n'
        "EOF\n"
        "  /* block\n"
        "     comment */\n"
        '  name = "p" # end\n'
        "}\n"
    )
    assert not f.diagnostics, f.diagnostics
    (blk,) = f.blocks
    assert blk.attribute("policy") is not None
    assert blk.attribute("name").value == '"p"'


def test_string_with_braces_and_interpolation() -> None:
    f = parse_hcl(
        'resource "x_y" "z" {\n'
        '  a = "${jsonencode({k = "v"})}"\n'
        '  b = "literal } brace"\n'
        "}\n"
    )
    assert not f.diagnostics, f.diagnostics
    (blk,) = f.blocks
    assert blk.attribute("b") is not None


def test_unterminated_string_reports_diagnostic_not_exception() -> None:
    f = parse_hcl(
        'resource "aws_s3_bucket" "b" {\n'
        '  bucket = "oops\n'
        "}\n"
    )
    assert f.diagnostics
    assert any(
        d.code in ("unterminated-string", "unterminated-block")
        for d in f.diagnostics
    )


def test_stray_brace_is_diagnostic_and_parse_continues() -> None:
    f = parse_hcl(
        "}\n"
        'resource "aws_s3_bucket" "ok" {\n'
        '  bucket = "fine"\n'
        "}\n"
    )
    assert any(d.code == "stray-brace" for d in f.diagnostics)
    assert any(
        b.labels == ("aws_s3_bucket", "ok") for b in f.blocks
    )


def test_garbage_line_does_not_abort_parse() -> None:
    f = parse_hcl(
        'resource "aws_s3_bucket" "ok" {\n'
        "  !!!not-hcl!!!\n"
        '  bucket = "fine"\n'
        "}\n"
    )
    assert f.diagnostics
    (blk,) = f.blocks
    assert blk.attribute("bucket") is not None


def test_top_level_assignments_for_tfvars() -> None:
    f = parse_hcl('environment = "dev"\nreplicas = 2\n')
    assert not f.diagnostics
    assert [a.name for a in f.attributes] == ["environment", "replicas"]
