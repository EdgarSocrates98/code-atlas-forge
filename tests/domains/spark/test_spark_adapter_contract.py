"""Contrato do adapter ``pyspark``: protocolo, vocabulários, validação."""

from __future__ import annotations

from conftest import make_request

from code_atlas.contracts.enums import ConfidenceClass
from code_atlas.domains.contracts import DomainAdapter, DomainExtractionResult
from code_atlas.domains.model import DomainEntityKind, DomainRelationKind
from code_atlas.domains.registry import DomainRegistry
from code_atlas.domains.spark import PySparkAdapter, satisfies_protocol
from code_atlas.domains.validation import validate_extraction


class TestContract:
    def test_satisfies_adapter_protocol(
        self, adapter: PySparkAdapter
    ) -> None:
        assert isinstance(adapter, DomainAdapter)
        assert adapter.domain_id == "pyspark"
        assert adapter.adapter_version
        assert isinstance(satisfies_protocol(), DomainAdapter)

    def test_supported_artifacts(self, adapter: PySparkAdapter) -> None:
        s = adapter.supported_artifacts()
        for good in ("etl.py", "jobs/etl_orders.py", "a/b/c.py"):
            assert s.matches(good), good
        for bad in ("model.sql", "README.md", "nb.ipynb", ""):
            assert not s.matches(bad), bad

    def test_declared_vocabularies(self, adapter: PySparkAdapter) -> None:
        kinds = adapter.entity_kinds()
        assert {
            DomainEntityKind.DATA_TABLE,
            DomainEntityKind.DATA_VIEW,
            DomainEntityKind.DATA_COLUMN,
            DomainEntityKind.ICEBERG_TABLE,
            DomainEntityKind.DATA_TRANSFORMATION,
            DomainEntityKind.SPARK_JOB,
        } <= kinds
        assert adapter.relation_kinds() == frozenset(
            {
                str(DomainRelationKind.READS_FROM),
                str(DomainRelationKind.WRITES_TO),
                str(DomainRelationKind.TRANSFORMS),
                str(DomainRelationKind.DERIVES_FROM),
                "CONTAINS",
            }
        )

    def test_confidence_model_forbids_inferred(
        self, adapter: PySparkAdapter
    ) -> None:
        cm = adapter.confidence_model()
        assert not cm.admits(ConfidenceClass.INFERRED)
        assert cm.admits(ConfidenceClass.SYNTACTIC)
        assert not cm.admits(ConfidenceClass.RESOLVED)

    def test_discovery_rules_cover_py(
        self, adapter: PySparkAdapter
    ) -> None:
        globs = {r.glob for r in adapter.discovery_rules()}
        assert "*.py" in globs

    def test_registry_explicit_register(
        self, adapter: PySparkAdapter
    ) -> None:
        reg = DomainRegistry()
        reg.register(adapter)
        assert reg.is_enabled("pyspark")
        assert reg.require("pyspark") is adapter

    def test_extract_never_raises_on_empty_request(
        self, adapter: PySparkAdapter
    ) -> None:
        res = adapter.extract(make_request(paths=()))
        assert isinstance(res, DomainExtractionResult)
        assert res.entities == ()
        assert res.relations == ()

    def test_extract_never_raises_on_missing_file(
        self, adapter: PySparkAdapter
    ) -> None:
        res = adapter.extract(make_request(paths=("nao/existe.py",)))
        codes = [d.code for d in res.diagnostics]
        assert "read-error" in codes

    def test_non_py_artifact_is_diagnosed(
        self, adapter: PySparkAdapter
    ) -> None:
        res = adapter.extract(make_request(paths=("README.md",)))
        codes = [d.code for d in res.diagnostics]
        assert "unsupported-artifact" in codes
        assert res.entities == ()

    def test_result_passes_contract_validation(
        self,
        adapter: PySparkAdapter,
        full_result: DomainExtractionResult,
    ) -> None:
        errors = validate_extraction(adapter, make_request(), full_result)
        assert errors == []


class TestEvidence:
    def test_extractor_tag(
        self, full_result: DomainExtractionResult
    ) -> None:
        assert full_result.evidence
        for ev in full_result.evidence:
            assert ev.extractor == "domain:pyspark"

    def test_every_entity_has_evidence(
        self, full_result: DomainExtractionResult
    ) -> None:
        for e in full_result.entities:
            assert e.evidence, e.identity.display()

    def test_every_relation_has_evidence(
        self, full_result: DomainExtractionResult
    ) -> None:
        for r in full_result.relations:
            assert r.evidence

    def test_identity_domain_tagged(
        self, full_result: DomainExtractionResult
    ) -> None:
        for e in full_result.entities:
            assert e.identity.domain == "pyspark"
        for r in full_result.relations:
            if r.from_identity is not None:
                assert r.from_identity.domain == "pyspark"
            if r.to_identity is not None:
                assert r.to_identity.domain == "pyspark"

    def test_evidence_points_at_real_paths(
        self, full_result: DomainExtractionResult
    ) -> None:
        paths = {ev.path for ev in full_result.evidence}
        assert paths <= set(  # só artefatos do request
            {
                "broken_job.py", "dynamic_job.py", "etl_orders.py",
                "notebook_implicit.py", "sql_job.py", "views_job.py",
            }
        )

    def test_no_resolved_without_candidate(
        self, full_result: DomainExtractionResult
    ) -> None:
        """Resolução honesta: adapter nunca declara ``resolved`` local."""
        for r in full_result.relations:
            if r.resolution is None:
                continue
            assert r.resolution.status.value != "resolved" or (
                r.to_identity is not None or r.to_symbol
            )
