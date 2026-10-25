"""Artefatos de configuração — EMR, Glue defs, triggers, spark-submit."""

from __future__ import annotations

from conftest import (
    EMR_BAD,
    EMR_CLUSTER,
    EMR_STEPS,
    JOB_DEF,
    SPARK_CONF,
    SUBMIT_SH,
    TRIGGER,
)


def test_emr_steps_emit_spark_jobs_and_order(extract, ents, rels) -> None:
    res = extract([EMR_STEPS])
    steps = {e.name: e for e in res.entities if e.attributes.get("artifact_kind") == "emr-step"}
    assert {"ingest-orders", "compact-orders", "notify"} <= set(steps)
    assert steps["ingest-orders"].kind.value == "spark_job"
    assert steps["compact-orders"].kind.value == "spark_job"
    # ordem de submissão é fato estático → DEPENDS_ON sequencial
    deps = rels(res, "DEPENDS_ON")
    assert any(
        frm == "aws-data:emr.steps#compact-orders" and to == "aws-data:emr.steps#ingest-orders"
        for frm, to, _s in deps
    )
    # spark-submit interno: job + script s3 referenciado
    contains = rels(res, "CONTAINS")
    assert any(
        frm == "aws-data:emr.steps#ingest-orders" and to == "aws-data:emr.ingest-orders"
        for frm, to, _s in contains
    )
    refs = rels(res, "REFERENCES")
    assert any(to == "aws-data:s3://jobs-bucket/scripts/ingest.py" for _f, to, _s in refs)
    # catálogo declarado via --conf dentro do step
    cats = {c.name for c in ents(res, "data_catalog")}
    assert "lake" in cats


def test_emr_cluster_yaml_roles(extract, ents, rels) -> None:
    res = extract([EMR_CLUSTER])
    clusters = [
        e
        for e in ents(res, "data_pipeline")
        if e.attributes.get("artifact_kind") == "emr-cluster-config"
    ]
    assert clusters
    assumes = rels(res, "ASSUMES_ROLE")
    targets = {to for _f, to, _s in assumes}
    assert "aws-data:aws:iam:123456789012.role/EMR_EC2_DefaultRole" in targets
    assert "aws-data:aws:iam:123456789012.role/EMR_DefaultRole" in targets
    assert all(_s == "external" for _f, _t, _s in assumes)


def test_glue_job_definition_role_and_script(extract, ents, rels) -> None:
    res = extract([JOB_DEF])
    jobs = [e for e in ents(res, "glue_job") if e.name == "nightly-etl"]
    assert jobs
    job = jobs[0]
    assert job.attributes.get("command") == "glueetl"
    # segredos nunca persistem valor
    assert job.attributes.get("arg.--extra_key") == "[REDACTED]"
    assumes = rels(res, "ASSUMES_ROLE")
    assert (
        "aws-data:jobs.nightly-etl",
        "aws-data:aws:iam:123456789012.role/GlueServiceRole",
        "external",
    ) in assumes
    refs = rels(res, "REFERENCES")
    assert any(to == "aws-data:s3://glue-scripts-bucket/nightly.py" for _f, to, _s in refs)
    assert any(to == "aws-data:s3://etl-out-bucket/final" for _f, to, _s in refs)


def test_trigger_triggers_job_external(extract, ents, rels) -> None:
    res = extract([TRIGGER])
    trigs = [
        e for e in ents(res, "data_pipeline") if e.attributes.get("artifact_kind") == "glue-trigger"
    ]
    assert trigs and trigs[0].attributes.get("trigger_type") == ("SCHEDULED")
    fires = rels(res, "TRIGGERS")
    assert any(to == "glue-job:nightly-etl" and _s == "external" for _f, to, _s in fires)
    deps = rels(res, "DEPENDS_ON")
    assert any(to == "glue-job:upstream-loader" and _s == "external" for _f, to, _s in deps)


def test_trigger_resolves_to_known_job(extract, rels) -> None:
    # no mesmo request, o job é extraído → TRIGGERS resolve
    res = extract([JOB_DEF, TRIGGER])
    fires = rels(res, "TRIGGERS")
    assert any(to == "aws-data:jobs.nightly-etl" and _s == "resolved" for _f, to, _s in fires)


def test_spark_submit_shell(extract, ents, rels) -> None:
    res = extract([SUBMIT_SH])
    jobs = ents(res, "spark_job")
    assert jobs
    refs = rels(res, "REFERENCES")
    assert any(to == "aws-data:s3://jobs-bucket/scripts/ingest.py" for _f, to, _s in refs)
    cats = {c.name for c in ents(res, "data_catalog")}
    assert "lake" in cats


def test_spark_conf_catalog(extract, ents, rels) -> None:
    res = extract([SPARK_CONF])
    cats = ents(res, "data_catalog")
    assert cats and cats[0].name == "lake"
    assert cats[0].attributes.get("engine") == "iceberg"
    refs = rels(res, "REFERENCES")
    assert any(to == "aws-data:s3://lake-wh/warehouse" for _f, to, _s in refs)


def test_malformed_json_is_diagnostic(extract, diag_codes) -> None:
    res = extract([EMR_BAD])
    assert "malformed-config" in diag_codes(res)
    errs = [d for d in res.diagnostics if d.severity == "error"]
    assert errs


def test_non_aws_json_is_info_diagnostic(extract, diag_codes, tmp_path) -> None:
    import json as _json

    p = tmp_path / "plain.json"
    p.write_text(_json.dumps({"unrelated": [1, 2, 3]}))
    from code_atlas.domains.aws import AwsDataAdapter
    from code_atlas.domains.contracts import (
        DiscoveredArtifact,
        DomainExtractionRequest,
    )

    res = AwsDataAdapter(base_path=tmp_path).extract(
        DomainExtractionRequest(
            repository="r",
            artifacts=(DiscoveredArtifact(path="plain.json"),),
        )
    )
    assert "not-aws-artifact" in diag_codes(res)
    assert res.entities == ()
