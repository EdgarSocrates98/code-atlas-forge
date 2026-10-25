"""Redação de segredos — casos adversariais (lf-0115, hard requirement).

``variable "db_password" { default = "sup3r-s3cret-do-not-leak" }`` e
amigos NUNCA podem aparecer em nenhuma entidade, atributo, evidência,
relação ou diagnóstico — nem o nome da variável some (auditável), só
o valor.
"""

from __future__ import annotations

from code_atlas.domains.contracts import (
    DiscoveredArtifact,
    DomainExtractionRequest,
    DomainExtractionResult,
)
from code_atlas.domains.terraform import TerraformAdapter
from code_atlas.domains.terraform.refs import (
    is_sensitive_name,
    redact_if_sensitive,
    sanitize_value,
)

SECRETS = (
    "sup3r-s3cret-do-not-leak",
    "tok-live-adversarial",
    "tfvars-secret-must-not-leak",
)


def _all_strings(res: DomainExtractionResult) -> list[str]:
    """Todo texto que o resultado carrega — varredura completa."""
    out: list[str] = []
    for e in res.entities:
        out.append(e.name)
        out.append(e.identity.qualified_name)
        out.extend(e.attributes.keys())
        out.extend(e.attributes.values())
    for r in res.relations:
        out.append(r.relation_type)
        out.append(r.to_unresolved)
        if r.resolution:
            out.append(r.resolution.unresolved_text)
    for ev in res.evidence:
        out.extend((ev.path, ev.notes, ev.extractor))
    for d in res.diagnostics:
        out.extend((d.code, d.message, d.path))
    return out


def test_secret_defaults_never_leak(
    full_result: DomainExtractionResult,
) -> None:
    blob = "\n".join(_all_strings(full_result))
    for secret in SECRETS:
        assert secret not in blob


def test_sensitive_variables_are_listed_but_redacted(
    full_result: DomainExtractionResult,
) -> None:
    root = next(
        e for e in full_result.entities
        if e.identity.qualified_name == "module.root"
    )
    sensitive = root.attributes["sensitive_variables"].split(",")
    assert "db_password" in sensitive
    assert "api_token" in sensitive
    # nomes presentes, valores nunca
    assert "variables" in root.attributes
    assert "sup3r-s3cret" not in "".join(root.attributes.values())
    assert full_result.stats["secrets_redacted"] >= 3


def test_tfvars_secret_never_leaks(
    full_result: DomainExtractionResult,
) -> None:
    # dev.tfvars tem db_password = "tfvars-secret-must-not-leak"
    blob = "\n".join(_all_strings(full_result))
    assert "tfvars-secret-must-not-leak" not in blob


def test_no_raw_interpolation_in_stored_values(
    full_result: DomainExtractionResult,
) -> None:
    for e in full_result.entities:
        for k, v in e.attributes.items():
            assert "${" not in v or "${dynamic}" in v, (k, v)
    for r in full_result.relations:
        if "${" in r.to_unresolved:
            assert "${dynamic}" in r.to_unresolved


def test_is_sensitive_name_pattern() -> None:
    for name in (
        "password", "db_password", "api_secret", "TOKEN",
        "key_pair", "aws_credential", "client_secret",
    ):
        assert is_sensitive_name(name), name
    for safe in ("environment", "bucket", "ami_id", "replicas"):
        assert not is_sensitive_name(safe), safe


def test_redact_and_sanitize_units() -> None:
    assert redact_if_sensitive("password", "hunter2") == "[REDACTED]"
    assert (
        redact_if_sensitive("env", "dev-${aws_s3_bucket.b.id}")
        == "dev-${dynamic}"
    )
    assert sanitize_value("a-${x}-b-${y}") == "a-${dynamic}-b-${dynamic}"


def test_adversarial_variable_with_secret_suffix(tmp_path) -> None:
    """``default`` de var sensível em arquivo próprio — nunca vaza."""
    (tmp_path / "v.tf").write_text(
        'variable "master_key" {\n'
        '  default = "MASTER-KEY-9x"\n'
        "}\n"
        'resource "aws_s3_bucket" "b" {\n'
        '  bucket = "ok"\n'
        "}\n"
    )
    adapter = TerraformAdapter(base_path=tmp_path)
    res = adapter.extract(
        DomainExtractionRequest(
            repository="r",
            artifacts=(DiscoveredArtifact(path="v.tf"),),
        )
    )
    blob = "\n".join(_all_strings(res))
    assert "MASTER-KEY-9x" not in blob
    assert res.stats["secrets_redacted"] >= 1
