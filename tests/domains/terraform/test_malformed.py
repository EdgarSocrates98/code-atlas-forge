"""HCL malformado → extração parcial + diagnósticos, nunca exceção."""

from __future__ import annotations

from conftest import BROKEN_PATHS, FIXTURE, REPO

from code_atlas.domains.contracts import (
    DiscoveredArtifact,
    DomainExtractionRequest,
)
from code_atlas.domains.terraform import TerraformAdapter


def _extract(paths: tuple[str, ...], base=FIXTURE):
    return TerraformAdapter(base_path=base).extract(
        DomainExtractionRequest(
            repository=REPO,
            artifacts=tuple(DiscoveredArtifact(path=p) for p in paths),
        )
    )


def test_malformed_tf_produces_partial_plus_diagnostics() -> None:
    res = _extract(BROKEN_PATHS)
    # diagnósticos estruturados, nunca exceção
    assert res.diagnostics
    codes = {d.code for d in res.diagnostics}
    assert codes & {
        "unterminated-string", "unterminated-block", "stray-brace",
        "parse-error",
    }
    assert all(d.adapter_id == "terraform" for d in res.diagnostics)
    # parcial: o bloco válido de stray.tf sobrevive
    qnames = {e.identity.qualified_name for e in res.entities}
    assert "aws_s3_bucket.survivor" in qnames


def test_unreadable_artifact_is_diagnostic() -> None:
    res = _extract(("ghost/missing.tf",))
    assert any(d.code == "artifact-unreadable" for d in res.diagnostics)


def test_unsupported_artifact_is_diagnostic() -> None:
    res = _extract(("main.tf", "notes.md"))
    assert any(d.code == "unsupported-artifact" for d in res.diagnostics)


def test_broken_files_do_not_poison_sibling_artifacts() -> None:
    res = _extract((*BROKEN_PATHS, "main.tf"))
    qnames = {e.identity.qualified_name for e in res.entities}
    assert "aws_s3_bucket.data" in qnames
    assert res.diagnostics  # diags do broken seguem presentes


def test_invalid_tfjson_is_diagnostic(tmp_path) -> None:
    (tmp_path / "bad.tf.json").write_text("{not json")
    res = _extract(("bad.tf.json",), base=tmp_path)
    assert any(d.code == "json-parse-error" for d in res.diagnostics)


def test_invalid_tfvars_is_diagnostic(tmp_path) -> None:
    (tmp_path / "bad.tfvars").write_text('x = "unterminated\n')
    res = _extract(("bad.tfvars",), base=tmp_path)
    assert res.diagnostics
