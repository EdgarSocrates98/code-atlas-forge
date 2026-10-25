"""Vocabulário e invariantes do modelo unificado de domínio (lf-0110)."""

from __future__ import annotations

import pytest

from code_atlas.contracts.enums import ConfidenceClass, EdgeKind, ResolutionStatus
from code_atlas.domains import (
    DOMAIN_RELATION_TYPES,
    DomainEntity,
    DomainEntityKind,
    DomainIdentity,
    DomainRelation,
    DomainRelationKind,
    DomainResolution,
)


def _ident(**kw: object) -> DomainIdentity:
    base = {
        "domain": "sql",
        "repository": "git-remote:git@x/r",
        "qualified_name": "analytics.orders",
    }
    base.update(kw)
    return DomainIdentity(**base)  # type: ignore[arg-type]


def test_entity_kind_vocabulary_is_closed() -> None:
    assert {k.value for k in DomainEntityKind} == {
        "data_table", "data_column", "data_view", "data_transformation",
        "data_pipeline", "spark_job", "glue_job", "data_catalog",
        "iceberg_table", "api_endpoint", "api_service", "api_contract",
        "terraform_resource", "terraform_module", "iam_role",
        "cicd_workflow", "cicd_job", "repository", "project",
    }


def test_relation_kind_vocabulary_is_closed() -> None:
    assert {k.value for k in DomainRelationKind} == {
        "READS_FROM", "WRITES_TO", "TRANSFORMS", "DERIVES_FROM",
        "DEPENDS_ON", "CALLS_ENDPOINT", "IMPLEMENTS_CONTRACT",
        "PROVISIONS", "USES_RESOURCE", "ASSUMES_ROLE", "TRIGGERS",
        "DEPLOYS", "TESTS", "REFERENCES",
    }
    # união com o vocabulário estrutural — CONTAINS/IMPORTS etc. válidos
    assert frozenset(
        {k.value for k in DomainRelationKind}
        | {k.value for k in EdgeKind}
    ) == DOMAIN_RELATION_TYPES


def test_entity_kind_coercion_and_validation() -> None:
    e = DomainEntity(identity=_ident(), kind="data_table", name="orders")
    assert e.kind is DomainEntityKind.DATA_TABLE
    assert e.domain == "sql"
    assert e.qualified_name == "analytics.orders"
    with pytest.raises(ValueError, match="invalid kind"):
        DomainEntity(identity=_ident(), kind="magic_beans", name="x")  # type: ignore[arg-type]


def test_entity_requires_name() -> None:
    with pytest.raises(ValueError, match="requires name"):
        DomainEntity(
            identity=_ident(), kind=DomainEntityKind.DATA_TABLE, name=" "
        )


def test_entity_attributes_coerced_to_str() -> None:
    e = DomainEntity(
        identity=_ident(), kind=DomainEntityKind.DATA_TABLE, name="t",
        attributes={"columns": 3},  # type: ignore[dict-item]
    )
    assert e.attributes == {"columns": "3"}


def _rel(**kw: object) -> DomainRelation:
    base: dict[str, object] = {
        "snapshot_id": 1,
        "relation_type": "READS_FROM",
        "resolver": "domain:sql",
        "confidence_class": ConfidenceClass.SYNTACTIC,
        "resolution_status": ResolutionStatus.RESOLVED,
        "from_entity_occ_id": 10,
        "to_entity_occ_id": 11,
    }
    base.update(kw)
    return DomainRelation(**base)  # type: ignore[arg-type]


def test_relation_validates_vocabulary() -> None:
    assert _rel().relation_type == "READS_FROM"
    assert _rel(relation_type="CONTAINS").relation_type == "CONTAINS"
    with pytest.raises(ValueError, match="invalid relation_type"):
        _rel(relation_type="MAKES_COFFEE")


def test_relation_requires_from_endpoint() -> None:
    with pytest.raises(ValueError, match="from endpoint"):
        _rel(from_entity_occ_id=None)


def test_relation_from_symbol_occurrence_accepted() -> None:
    """Ponte código→domínio: origem pode ser ocorrência de símbolo."""
    r = _rel(from_entity_occ_id=None, from_symbol_occ_id=7)
    assert r.from_symbol_occ_id == 7


def test_relation_requires_target() -> None:
    with pytest.raises(ValueError, match="to endpoint or to_unresolved"):
        _rel(to_entity_occ_id=None)
    # alvo textual honesto é suficiente
    r = _rel(to_entity_occ_id=None, to_unresolved="external.orders")
    assert r.to_unresolved == "external.orders"


def test_relation_requires_resolver_provenance() -> None:
    with pytest.raises(ValueError, match="resolver"):
        _rel(resolver="")


def test_inferred_can_never_be_resolved() -> None:
    """Invariante central da spec — enforcement no modelo."""
    with pytest.raises(ValueError, match="inferred"):
        _rel(
            confidence_class=ConfidenceClass.INFERRED,
            resolution_status=ResolutionStatus.RESOLVED,
        )
    # inferred + unresolved é legítimo
    r = _rel(
        confidence_class=ConfidenceClass.INFERRED,
        resolution_status=ResolutionStatus.UNRESOLVED,
        to_entity_occ_id=None,
        to_unresolved="maybe.orders",
    )
    assert r.confidence_class is ConfidenceClass.INFERRED


def _res(**kw: object) -> DomainResolution:
    base: dict[str, object] = {
        "status": ResolutionStatus.UNRESOLVED,
        "confidence_class": ConfidenceClass.SYNTACTIC,
        "resolver": "domain:sql",
        "unresolved_text": "x",
    }
    base.update(kw)
    return DomainResolution(**base)  # type: ignore[arg-type]


def test_resolution_resolved_requires_target() -> None:
    with pytest.raises(ValueError, match="requires target"):
        _res(status=ResolutionStatus.RESOLVED)
    ok = _res(status=ResolutionStatus.RESOLVED, target=_ident())
    assert ok.target is not None


def test_resolution_ambiguous_requires_two_candidates() -> None:
    with pytest.raises(ValueError, match=">=2 candidates"):
        _res(status=ResolutionStatus.AMBIGUOUS, candidates=(_ident(),))
    ok = _res(
        status=ResolutionStatus.AMBIGUOUS,
        candidates=(_ident(), _ident(catalog="other")),
    )
    assert len(ok.candidates) == 2


def test_resolution_unresolved_cannot_carry_target() -> None:
    with pytest.raises(ValueError, match="cannot carry a target"):
        _res(status=ResolutionStatus.UNRESOLVED, target=_ident())


def test_resolution_external_may_carry_target() -> None:
    """Identidade externa conhecida (ARN real, repo federado) é legítima."""
    ok = _res(status=ResolutionStatus.EXTERNAL, target=_ident())
    assert ok.status is ResolutionStatus.EXTERNAL


def test_resolution_inferred_never_resolved() -> None:
    with pytest.raises(ValueError, match="inferred"):
        _res(
            status=ResolutionStatus.RESOLVED,
            target=_ident(),
            confidence_class=ConfidenceClass.INFERRED,
        )


def test_resolution_requires_resolver() -> None:
    with pytest.raises(ValueError, match="resolver"):
        _res(resolver="")
