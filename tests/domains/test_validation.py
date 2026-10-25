"""validate_extraction: contrato declarado vs. resultado (lf-0110)."""

from __future__ import annotations

import pytest

from code_atlas.contracts.enums import ConfidenceClass, ResolutionStatus
from code_atlas.domains import (
    ArtifactSupport,
    ConfidenceModel,
    DiscoveredArtifact,
    DiscoveryRule,
    DomainEntityKind,
    DomainExtractionRequest,
    DomainExtractionResult,
    DomainIdentity,
    DomainResolution,
    ExtractedDomainEntity,
    ExtractedDomainRelation,
    confidence_rank,
    validate_extraction,
)
from code_atlas.domains.evidence import evidence_record

REPO = "git-remote:git@x/r"


class _Adapter:
    def __init__(
        self,
        entity_kinds: frozenset[DomainEntityKind] = frozenset(
            {DomainEntityKind.DATA_TABLE}
        ),
        relation_kinds: frozenset[str] = frozenset({"READS_FROM"}),
        ceiling: ConfidenceClass = ConfidenceClass.SYNTACTIC,
    ) -> None:
        self._ek = entity_kinds
        self._rk = relation_kinds
        self._cm = ConfidenceModel(ceiling=ceiling)

    @property
    def domain_id(self) -> str:
        return "sql"

    @property
    def adapter_version(self) -> str:
        return "0.1.0"

    def supported_artifacts(self) -> ArtifactSupport:
        return ArtifactSupport(extensions=frozenset({".sql"}))

    def entity_kinds(self) -> frozenset[DomainEntityKind]:
        return self._ek

    def relation_kinds(self) -> frozenset[str]:
        return self._rk

    def confidence_model(self) -> ConfidenceModel:
        return self._cm

    def discovery_rules(self) -> tuple[DiscoveryRule, ...]:
        return ()

    def extract(
        self, request: DomainExtractionRequest
    ) -> DomainExtractionResult:
        return DomainExtractionResult("sql", "0.1.0")


def _req() -> DomainExtractionRequest:
    return DomainExtractionRequest(
        repository=REPO,
        artifacts=(DiscoveredArtifact(path="q.sql"),),
    )


def _ident(**kw: object) -> DomainIdentity:
    base: dict[str, object] = {
        "domain": "sql", "repository": REPO, "qualified_name": "db.orders",
    }
    base.update(kw)
    return DomainIdentity(**base)  # type: ignore[arg-type]


def test_clean_result_passes() -> None:
    result = DomainExtractionResult(
        domain_id="sql", adapter_version="0.1.0",
        entities=(
            ExtractedDomainEntity(
                identity=_ident(), kind=DomainEntityKind.DATA_TABLE,
                name="orders", path="q.sql",
            ),
        ),
    )
    assert validate_extraction(_Adapter(), _req(), result) == []


def test_domain_id_mismatch_flagged() -> None:
    result = DomainExtractionResult(domain_id="terraform", adapter_version="0.1.0")
    errors = validate_extraction(_Adapter(), _req(), result)
    assert any("domain_id" in e for e in errors)


def test_undeclared_entity_kind_flagged() -> None:
    result = DomainExtractionResult(
        domain_id="sql", adapter_version="0.1.0",
        entities=(
            ExtractedDomainEntity(
                identity=_ident(), kind=DomainEntityKind.IAM_ROLE,
                name="r", path="q.sql",
            ),
        ),
    )
    errors = validate_extraction(_Adapter(), _req(), result)
    assert any("undeclared entity kind" in e for e in errors)


def test_undeclared_relation_kind_flagged() -> None:
    src = _ident()
    result = DomainExtractionResult(
        domain_id="sql", adapter_version="0.1.0",
        relations=(
            ExtractedDomainRelation(
                relation_type="WRITES_TO", path="q.sql",
                from_identity=src, to_unresolved="x",
            ),
        ),
    )
    errors = validate_extraction(_Adapter(), _req(), result)
    assert any("undeclared relation kind" in e for e in errors)


def test_entity_from_wrong_repo_or_domain_flagged() -> None:
    result = DomainExtractionResult(
        domain_id="sql", adapter_version="0.1.0",
        entities=(
            ExtractedDomainEntity(
                identity=_ident(repository="other-repo"),
                kind=DomainEntityKind.DATA_TABLE, name="o", path="q.sql",
            ),
            ExtractedDomainEntity(
                identity=_ident(domain="terraform"),
                kind=DomainEntityKind.DATA_TABLE, name="o2", path="q.sql",
            ),
        ),
    )
    errors = validate_extraction(_Adapter(), _req(), result)
    assert any("repository" in e for e in errors)
    assert any("entity domain" in e for e in errors)


def test_duplicate_entity_identity_flagged() -> None:
    dup = ExtractedDomainEntity(
        identity=_ident(), kind=DomainEntityKind.DATA_TABLE,
        name="orders", path="q.sql",
    )
    result = DomainExtractionResult(
        domain_id="sql", adapter_version="0.1.0",
        entities=(dup, dup),
    )
    errors = validate_extraction(_Adapter(), _req(), result)
    assert any("duplicate entity identity" in e for e in errors)


def test_confidence_above_ceiling_flagged() -> None:
    src = _ident()
    result = DomainExtractionResult(
        domain_id="sql", adapter_version="0.1.0",
        relations=(
            ExtractedDomainRelation(
                relation_type="READS_FROM", path="q.sql",
                from_identity=src, to_unresolved="x",
                resolution=DomainResolution(
                    status=ResolutionStatus.UNRESOLVED,
                    confidence_class=ConfidenceClass.RESOLVED,  # > ceiling
                    resolver="domain:sql",
                ),
            ),
        ),
    )
    errors = validate_extraction(_Adapter(), _req(), result)
    assert any("exceeds adapter ceiling" in e for e in errors)


def test_evidence_index_out_of_range_flagged() -> None:
    result = DomainExtractionResult(
        domain_id="sql", adapter_version="0.1.0",
        entities=(
            ExtractedDomainEntity(
                identity=_ident(), kind=DomainEntityKind.DATA_TABLE,
                name="o", path="q.sql", evidence=(0,),
            ),
        ),
        evidence=(),
    )
    errors = validate_extraction(_Adapter(), _req(), result)
    assert any("evidence index out of range" in e for e in errors)


def test_artifact_support_matching() -> None:
    sup = ArtifactSupport(
        extensions=frozenset({".sql"}), globs=("*.tf", "pipelines/*.yaml")
    )
    assert sup.matches("a/b.sql")
    assert sup.matches("main.tf")
    assert sup.matches("pipelines/x.yaml")
    # fnmatch stdlib: `*` atravessa `/` — documentado em ArtifactSupport
    assert sup.matches("sub/x.tf")
    assert not sup.matches("x.tf.bak")
    assert not sup.matches("")
    assert not sup.matches("README")


def test_confidence_model_invariants() -> None:
    cm = ConfidenceModel(
        ceiling=ConfidenceClass.SYNTACTIC,
        default=ConfidenceClass.HEURISTIC,
    )
    assert cm.admits(ConfidenceClass.INFERRED)
    assert cm.admits(ConfidenceClass.SYNTACTIC)
    assert not cm.admits(ConfidenceClass.RESOLVED)
    strict = ConfidenceModel(allows_inferred=False)
    assert not strict.admits(ConfidenceClass.INFERRED)
    with pytest.raises(ValueError, match="exceed ceiling"):
        ConfidenceModel(
            ceiling=ConfidenceClass.HEURISTIC,
            default=ConfidenceClass.RESOLVED,
        )
    assert confidence_rank(ConfidenceClass.RESOLVED) > confidence_rank(
        ConfidenceClass.INFERRED
    )


def test_evidence_record_domain_convention() -> None:
    rec = evidence_record(
        adapter_domain="sql", adapter_version="0.1.0",
        path="q.sql", content_hash="sha256:x",
    )
    assert rec.extractor == "domain:sql"
    assert rec.extractor_version == "0.1.0"
