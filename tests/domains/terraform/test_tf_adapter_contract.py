"""Contrato do adapter: suporte de artefatos, registry, validação."""

from __future__ import annotations

from conftest import make_request

from code_atlas.contracts.enums import ConfidenceClass
from code_atlas.domains.contracts import DomainAdapter, DomainExtractionResult
from code_atlas.domains.model import DomainEntityKind, DomainRelationKind
from code_atlas.domains.registry import DomainRegistry
from code_atlas.domains.terraform import TerraformAdapter
from code_atlas.domains.validation import validate_extraction


class TestContract:
    def test_satisfies_adapter_protocol(self, adapter: TerraformAdapter) -> None:
        assert isinstance(adapter, DomainAdapter)
        assert adapter.domain_id == "terraform"
        assert adapter.adapter_version

    def test_supported_artifacts(self, adapter: TerraformAdapter) -> None:
        s = adapter.supported_artifacts()
        for good in (
            "main.tf", "env/dev/main.tf", "m.tf.json", "dev.tfvars",
            "x.auto.tfvars", "vars.tfvars.json",
        ):
            assert s.matches(good), good
        for bad in ("main.py", "package.json", "README.md", ""):
            assert not s.matches(bad), bad

    def test_declared_vocabularies(self, adapter: TerraformAdapter) -> None:
        assert adapter.entity_kinds() == frozenset(
            {
                DomainEntityKind.TERRAFORM_RESOURCE,
                DomainEntityKind.TERRAFORM_MODULE,
                DomainEntityKind.IAM_ROLE,
            }
        )
        assert adapter.relation_kinds() == frozenset(
            {
                str(DomainRelationKind.DEPENDS_ON),
                str(DomainRelationKind.PROVISIONS),
                str(DomainRelationKind.USES_RESOURCE),
                str(DomainRelationKind.ASSUMES_ROLE),
                str(DomainRelationKind.REFERENCES),
            }
        )

    def test_confidence_model_forbids_inferred(
        self, adapter: TerraformAdapter
    ) -> None:
        cm = adapter.confidence_model()
        assert not cm.admits(ConfidenceClass.INFERRED)
        assert cm.admits(ConfidenceClass.SYNTACTIC)

    def test_registry_explicit_register(self, adapter: TerraformAdapter) -> None:
        reg = DomainRegistry()
        reg.register(adapter)
        assert reg.is_enabled("terraform")
        assert reg.require("terraform") is adapter

    def test_extract_never_raises_on_empty_request(
        self, adapter: TerraformAdapter
    ) -> None:
        res = adapter.extract(make_request(paths=()))
        assert isinstance(res, DomainExtractionResult)
        assert res.entities == ()
        assert res.relations == ()

    def test_result_passes_contract_validation(
        self, adapter: TerraformAdapter, full_result: DomainExtractionResult
    ) -> None:
        errors = validate_extraction(adapter, make_request(), full_result)
        assert errors == []
