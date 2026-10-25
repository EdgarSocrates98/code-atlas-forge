"""Contrato ``DomainAdapter`` do ``AwsDataAdapter`` (lf-0113)."""

from __future__ import annotations

from conftest import NOTES

from code_atlas.contracts.enums import ResolutionStatus
from code_atlas.domains.aws import AwsDataAdapter
from code_atlas.domains.contracts import (
    DomainAdapter,
    DomainExtractionRequest,
)
from code_atlas.domains.model import (
    DOMAIN_RELATION_TYPES,
    DomainEntityKind,
)


def test_satisfies_domain_adapter_protocol() -> None:
    assert isinstance(AwsDataAdapter(), DomainAdapter)


def test_domain_identity_and_version() -> None:
    adapter = AwsDataAdapter()
    assert adapter.domain_id == "aws-data"
    assert adapter.adapter_version


def test_supported_artifacts_cover_declared_shapes() -> None:
    support = AwsDataAdapter().supported_artifacts()
    for ext in (
        ".py",
        ".sql",
        ".json",
        ".yaml",
        ".yml",
        ".sh",
        ".conf",
        ".properties",
    ):
        assert ext in support.extensions, ext


def test_entity_kinds_are_domain_vocabulary() -> None:
    kinds = AwsDataAdapter().entity_kinds()
    assert kinds <= frozenset(DomainEntityKind)
    assert DomainEntityKind.GLUE_JOB in kinds
    assert DomainEntityKind.ICEBERG_TABLE in kinds
    assert DomainEntityKind.IAM_ROLE in kinds
    assert DomainEntityKind.SPARK_JOB in kinds


def test_relation_kinds_are_closed_vocabulary() -> None:
    kinds = AwsDataAdapter().relation_kinds()
    assert kinds <= DOMAIN_RELATION_TYPES
    assert {"READS_FROM", "WRITES_TO", "ASSUMES_ROLE", "TRIGGERS", "REFERENCES"} <= kinds


def test_confidence_model_is_evidence_first() -> None:
    model = AwsDataAdapter().confidence_model()
    assert model.allows_inferred is False
    assert model.ceiling.value == "syntactic"


def test_discovery_rules_are_optional() -> None:
    rules = AwsDataAdapter().discovery_rules()
    assert rules
    # nenhuma regra pode exigir artefato — todos opcionais
    assert all(r.required is False for r in rules)
    assert any("athena" in r.glob for r in rules)
    assert any(r.artifact_kind == "emr-step-config" for r in rules)
    assert any(r.artifact_kind == "glue-job-definition" for r in rules)


def test_empty_request_returns_empty_result() -> None:
    res = AwsDataAdapter().extract(DomainExtractionRequest(repository="r", artifacts=()))
    assert res.domain_id == "aws-data"
    assert res.entities == ()
    assert res.relations == ()
    assert res.diagnostics == ()


def test_unsupported_extension_is_diagnostic(extract, diag_codes) -> None:
    res = extract([NOTES])
    assert res.entities == ()
    assert "unsupported-artifact" in diag_codes(res)


def test_every_identity_uses_aws_domain(full_result) -> None:
    for e in full_result.entities:
        assert e.identity.domain == "aws-data", e.identity.display()
        assert e.evidence, e.identity.display()


def test_no_inferred_resolved_relations(full_result) -> None:
    for r in full_result.relations:
        res = r.resolution
        assert res is not None
        assert res.resolver == "domain:aws-data"
        if res.status is ResolutionStatus.RESOLVED:
            assert res.target is not None
        if res.status is ResolutionStatus.UNRESOLVED:
            assert res.target is None
