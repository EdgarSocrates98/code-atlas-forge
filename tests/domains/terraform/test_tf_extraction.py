"""Extração do adapter Terraform sobre a fixture terraform_infra."""

from __future__ import annotations

from conftest import make_request

from code_atlas.domains.contracts import DomainExtractionResult
from code_atlas.domains.model import DomainEntityKind
from code_atlas.domains.terraform import TerraformAdapter


def _rel_set(res: DomainExtractionResult) -> set[tuple[str, str, str]]:
    """(from_qname, tipo, to_qname|to_unresolved) para asserts diretos."""
    out: set[tuple[str, str, str]] = set()
    for r in res.relations:
        frm = (
            r.from_identity.qualified_name if r.from_identity else r.from_symbol
        )
        tgt = r.to_identity.qualified_name if r.to_identity else r.to_unresolved
        out.add((frm, r.relation_type, tgt))
    return out


def _entity(res: DomainExtractionResult, qname: str, ns: str = ""):
    for e in res.entities:
        if (
            e.identity.qualified_name == qname
            and e.identity.namespace == ns
        ):
            return e
    raise AssertionError(f"entity not found: {ns}|{qname}")


class TestEntities:
    def test_resources_extracted_by_kind(
        self, full_result: DomainExtractionResult
    ) -> None:
        qnames = {e.identity.qualified_name for e in full_result.entities}
        for expected in (
            "aws_s3_bucket.data", "aws_glue_job.etl",
            "aws_lambda_function.trigger", "aws_cloudwatch_log_group.lambda",
            "random_id.suffix", "aws_iam_policy.glue_s3",
            "aws_iam_role_policy_attachment.glue",
            "aws_iam_role_policy.extra", "aws_sfn_state_machine.pipeline",
            "aws_sqs_queue.workers", "aws_cloudwatch_metric_alarm.depth",
            "aws_emr_cluster.analytics", "data.aws_iam_policy_document.extra",
            "data.terraform_remote_state.network",
        ):
            assert expected in qnames, expected

    def test_iam_roles_have_dedicated_kind(
        self, full_result: DomainExtractionResult
    ) -> None:
        roles = {
            e.identity.qualified_name
            for e in full_result.entities
            if e.kind is DomainEntityKind.IAM_ROLE
        }
        assert roles == {"aws_iam_role.glue", "aws_iam_role.lambda_exec"}

    def test_bounded_class_catalog_and_generic_fallback(
        self, full_result: DomainExtractionResult
    ) -> None:
        assert _entity(full_result, "aws_s3_bucket.data").attributes[
            "resource_class"
        ] == "s3"
        assert _entity(full_result, "aws_glue_job.etl").attributes[
            "resource_class"
        ] == "glue"
        assert _entity(full_result, "aws_emr_cluster.analytics").attributes[
            "resource_class"
        ] == "emr"
        # fora do catálogo fechado → genérico declarado, nunca descartado
        assert _entity(full_result, "random_id.suffix").attributes[
            "resource_class"
        ] == "other"

    def test_module_tree_identities(self, full_result: DomainExtractionResult) -> None:
        flow = _entity(
            full_result, "aws_s3_bucket.flow_logs", ns="module.network"
        )
        assert flow.attributes["module_path"] == "module.network"
        net = _entity(full_result, "module.network")
        assert net.kind is DomainEntityKind.TERRAFORM_MODULE
        assert net.attributes["source_kind"] == "local"
        root = _entity(full_result, "module.root")
        assert root.attributes["module_role"] == "root"

    def test_environment_static_from_variable_default(
        self, full_result: DomainExtractionResult
    ) -> None:
        ent = _entity(full_result, "aws_s3_bucket.data")
        assert ent.identity.environment == "dev"
        assert ent.attributes["env_source"] == "variable-default"

    def test_request_environment_overrides(
        self, adapter: TerraformAdapter
    ) -> None:
        res = adapter.extract(make_request(environment="prod"))
        ent = _entity(res, "aws_s3_bucket.data")
        assert ent.identity.environment == "prod"
        assert ent.attributes["env_source"] == "request"

    def test_multiplicity_declared_never_expanded(
        self, full_result: DomainExtractionResult
    ) -> None:
        assert _entity(full_result, "aws_sqs_queue.workers").attributes[
            "multiplicity"
        ] == "dynamic"
        assert _entity(
            full_result, "aws_cloudwatch_metric_alarm.depth"
        ).attributes["multiplicity"] == "dynamic"
        # nenhuma instância fabricada
        assert not any(
            "[" in e.identity.qualified_name for e in full_result.entities
        )

    def test_remote_state_marked(self, full_result: DomainExtractionResult) -> None:
        rs = _entity(full_result, "data.terraform_remote_state.network")
        assert rs.attributes["mode"] == "data"
        assert rs.attributes["remote_state"] == "true"
        assert rs.attributes["backend"] == "s3"


class TestRelations:
    def test_explicit_and_implicit_depends_on(
        self, full_result: DomainExtractionResult
    ) -> None:
        rels = _rel_set(full_result)
        # explícito: depends_on = [aws_iam_role_policy_attachment.glue]
        assert (
            "aws_glue_job.etl", "DEPENDS_ON",
            "aws_iam_role_policy_attachment.glue",
        ) in rels
        # implícito: script_location referencia o bucket
        assert (
            "aws_glue_job.etl", "DEPENDS_ON", "aws_s3_bucket.data"
        ) in rels
        assert (
            "aws_cloudwatch_log_group.lambda", "DEPENDS_ON",
            "aws_lambda_function.trigger",
        ) in rels

    def test_provisions_local_module_and_root(
        self, full_result: DomainExtractionResult
    ) -> None:
        rels = _rel_set(full_result)
        assert (
            "module.network", "PROVISIONS", "aws_s3_bucket.flow_logs"
        ) in rels
        assert (
            "module.network", "PROVISIONS",
            "aws_cloudwatch_log_group.flow",
        ) in rels
        assert ("module.root", "PROVISIONS", "aws_s3_bucket.data") in rels

    def test_uses_resource_for_document_refs(
        self, full_result: DomainExtractionResult
    ) -> None:
        rels = _rel_set(full_result)
        assert (
            "aws_iam_policy.glue_s3", "USES_RESOURCE", "aws_s3_bucket.data"
        ) in rels
        assert (
            "data.aws_iam_policy_document.extra", "USES_RESOURCE",
            "aws_s3_bucket.data",
        ) in rels
        assert (
            "aws_sfn_state_machine.pipeline", "USES_RESOURCE",
            "aws_lambda_function.trigger",
        ) in rels

    def test_assumes_role_and_role_chain(
        self, full_result: DomainExtractionResult
    ) -> None:
        rels = _rel_set(full_result)
        assert (
            "aws_lambda_function.trigger", "ASSUMES_ROLE",
            "aws_iam_role.lambda_exec",
        ) in rels
        assert (
            "aws_glue_job.etl", "ASSUMES_ROLE", "aws_iam_role.glue"
        ) in rels
        # cadeia: lambda_exec confia em glue → glue pode assumi-la
        assert (
            "aws_iam_role.glue", "ASSUMES_ROLE", "aws_iam_role.lambda_exec"
        ) in rels
        # attachment vincula policy↔role — NÃO é assumes
        assert (
            "aws_iam_role_policy_attachment.glue", "ASSUMES_ROLE",
            "aws_iam_role.glue",
        ) not in rels

    def test_references_data_sources_and_external(
        self, full_result: DomainExtractionResult
    ) -> None:
        rels = _rel_set(full_result)
        assert (
            "aws_iam_role_policy.extra", "REFERENCES",
            "data.aws_iam_policy_document.extra",
        ) in rels
        assert (
            "data.terraform_remote_state.network", "REFERENCES",
            "remote-state:s3",
        ) in rels
        assert (
            "module.root", "REFERENCES", "backend:s3"
        ) in rels
        assert (
            "module.shared", "REFERENCES",
            "terraform-aws-modules/vpc/aws",
        ) in rels
        assert (
            "module.extra", "REFERENCES",
            "git::https://example.com/mod.git",
        ) in rels

    def test_external_resolution_status(
        self, full_result: DomainExtractionResult
    ) -> None:
        externals = [
            r for r in full_result.relations
            if r.resolution
            and str(r.resolution.status) == "external"
        ]
        assert externals, "expected external relations"
        assert all(r.to_unresolved for r in externals)
        resolved = [
            r for r in full_result.relations
            if r.resolution and str(r.resolution.status) == "resolved"
        ]
        assert resolved
        assert all(r.to_identity is not None for r in resolved)

    def test_unresolved_target_is_honest(
        self, adapter: TerraformAdapter, tmp_path
    ) -> None:
        (tmp_path / "main.tf").write_text(
            'resource "aws_lambda_function" "f" {\n'
            "  s3_bucket = aws_s3_bucket.absent.id\n"
            "}\n"
        )
        adapter = TerraformAdapter(base_path=tmp_path)
        res = adapter.extract(
            make_request(paths=("main.tf",))
        )
        unr = [
            r for r in res.relations
            if r.resolution
            and str(r.resolution.status) == "unresolved"
        ]
        assert unr
        assert unr[0].to_unresolved == "aws_s3_bucket.absent"

    def test_no_self_edges_or_fabricated_refs(
        self, full_result: DomainExtractionResult
    ) -> None:
        for r in full_result.relations:
            if r.from_identity and r.to_identity:
                assert (
                    r.from_identity.logical_key()
                    != r.to_identity.logical_key()
                ), r

    def test_arn_literal_is_external_reference(
        self, full_result: DomainExtractionResult
    ) -> None:
        rels = _rel_set(full_result)
        assert (
            "aws_emr_cluster.analytics", "REFERENCES",
            "arn:aws:iam::123456789012:role/EMR_DefaultRole",
        ) in rels
