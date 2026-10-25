"""Contrato do adapter: suporte de artefatos, registry, validação."""

from __future__ import annotations

from conftest import make_request

from code_atlas.contracts.enums import ConfidenceClass
from code_atlas.domains.contracts import DomainAdapter, DomainExtractionResult
from code_atlas.domains.model import DomainEntityKind, DomainRelationKind
from code_atlas.domains.registry import DomainRegistry
from code_atlas.domains.sql import SqlDomainAdapter
from code_atlas.domains.sql.adapter import looks_like_sql
from code_atlas.domains.validation import validate_extraction


class TestContract:
    def test_satisfies_adapter_protocol(
        self, adapter: SqlDomainAdapter
    ) -> None:
        assert isinstance(adapter, DomainAdapter)
        assert adapter.domain_id == "sql"
        assert adapter.adapter_version

    def test_supported_artifacts(self, adapter: SqlDomainAdapter) -> None:
        s = adapter.supported_artifacts()
        for good in (
            "etl.sql", "staging/02_staging.sql", "a/b/c.sql",
            "model.sql.j2",
        ):
            assert s.matches(good), good
        for bad in ("main.py", "README.md", "schema.json", ""):
            assert not s.matches(bad), bad

    def test_declared_vocabularies(self, adapter: SqlDomainAdapter) -> None:
        assert adapter.entity_kinds() == frozenset(
            {
                DomainEntityKind.DATA_TABLE,
                DomainEntityKind.DATA_VIEW,
                DomainEntityKind.DATA_COLUMN,
                DomainEntityKind.DATA_TRANSFORMATION,
            }
        )
        assert adapter.relation_kinds() == frozenset(
            {
                str(DomainRelationKind.READS_FROM),
                str(DomainRelationKind.WRITES_TO),
                str(DomainRelationKind.TRANSFORMS),
                str(DomainRelationKind.DERIVES_FROM),
            }
        )

    def test_confidence_model_forbids_inferred(
        self, adapter: SqlDomainAdapter
    ) -> None:
        cm = adapter.confidence_model()
        assert not cm.admits(ConfidenceClass.INFERRED)
        assert cm.admits(ConfidenceClass.SYNTACTIC)

    def test_discovery_rules_cover_sql(
        self, adapter: SqlDomainAdapter
    ) -> None:
        globs = {r.glob for r in adapter.discovery_rules()}
        assert "*.sql" in globs

    def test_registry_explicit_register(
        self, adapter: SqlDomainAdapter
    ) -> None:
        reg = DomainRegistry()
        reg.register(adapter)
        assert reg.is_enabled("sql")
        assert reg.require("sql") is adapter

    def test_extract_never_raises_on_empty_request(
        self, adapter: SqlDomainAdapter
    ) -> None:
        res = adapter.extract(make_request(paths=()))
        assert isinstance(res, DomainExtractionResult)
        assert res.entities == ()
        assert res.relations == ()

    def test_extract_never_raises_on_missing_file(
        self, adapter: SqlDomainAdapter
    ) -> None:
        res = adapter.extract(
            make_request(paths=("nao/existe.sql",))
        )
        codes = [d.code for d in res.diagnostics]
        assert "unreadable-artifact" in codes

    def test_result_passes_contract_validation(
        self,
        adapter: SqlDomainAdapter,
        full_result: DomainExtractionResult,
    ) -> None:
        errors = validate_extraction(adapter, make_request(), full_result)
        assert errors == []


class TestSniffing:
    def test_looks_like_sql_positive(self) -> None:
        assert looks_like_sql("SELECT a FROM t")
        assert looks_like_sql("-- comentário\nINSERT INTO t VALUES (1)")
        assert looks_like_sql("/* c */ CREATE TABLE t (a INT)")

    def test_looks_like_sql_negative(self) -> None:
        assert not looks_like_sql("def f(): pass")
        assert not looks_like_sql("notas soltas sem keyword")
        assert not looks_like_sql("")

    def test_non_sql_extension_sniffed_out(
        self, adapter: SqlDomainAdapter, tmp_path
    ) -> None:
        p = tmp_path / "notas.txt"
        p.write_text("isto não é sql de jeito nenhum", encoding="utf-8")
        res = SqlDomainAdapter(base_path=tmp_path).extract(
            make_request(paths=("notas.txt",))
        )
        assert "not-sql-artifact" in [d.code for d in res.diagnostics]
        assert res.entities == ()

    def test_non_sql_extension_with_sql_content_extracts(
        self, tmp_path
    ) -> None:
        p = tmp_path / "runbook.txt"
        p.write_text("SELECT a FROM t;", encoding="utf-8")
        res = SqlDomainAdapter(base_path=tmp_path).extract(
            make_request(paths=("runbook.txt",))
        )
        assert "not-sql-artifact" not in [d.code for d in res.diagnostics]
        assert any(
            e.kind == DomainEntityKind.DATA_TABLE for e in res.entities
        )


class TestEvidence:
    def test_extractor_tag(
        self, full_result: DomainExtractionResult
    ) -> None:
        assert full_result.evidence
        for ev in full_result.evidence:
            assert ev.extractor == "domain:sql"

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
            assert e.identity.domain == "sql"
        for r in full_result.relations:
            if r.from_identity is not None:
                assert r.from_identity.domain == "sql"
            if r.to_identity is not None:
                assert r.to_identity.domain == "sql"
