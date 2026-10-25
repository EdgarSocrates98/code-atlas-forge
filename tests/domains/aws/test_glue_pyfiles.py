"""Extração de scripts Glue/Spark ``.py`` — AST estático, zero execução."""

from __future__ import annotations

from conftest import BROKEN, CROSS_ACCOUNT, GLUE_ETL

from code_atlas.domains.model import DomainEntityKind


def test_glue_job_entity_and_markers(extract, ents) -> None:
    res = extract([GLUE_ETL])
    jobs = ents(res, "glue_job")
    assert len(jobs) == 1
    job = jobs[0]
    assert job.name == "orders_etl"
    assert job.attributes.get("job_init") == "true"
    assert job.attributes.get("job_commit_or_run") == "true"
    # bookmark: valor literal observado em job.init(args=…)
    assert job.attributes.get("job_bookmarks") == "job-bookmark-enable"
    assert "JOB_NAME" in (job.attributes.get("job_option_names") or "")


def test_catalog_read_and_write_edges(extract, rels) -> None:
    res = extract([GLUE_ETL])
    reads = rels(res, "READS_FROM")
    writes = rels(res, "WRITES_TO")
    job = "aws-data:glue/jobs.orders_etl"
    assert (job, "aws-data:aws:glue.sales_db.orders", "resolved") in reads
    assert (job, "aws-data:aws:glue.lake_db.agg_orders", "resolved") in writes


def test_s3_connection_options_paths(extract, rels) -> None:
    res = extract([GLUE_ETL])
    reads = rels(res, "READS_FROM")
    assert (
        "aws-data:glue/jobs.orders_etl",
        "aws-data:s3://raw-logs-bucket/orders/daily",
        "resolved",
    ) in reads


def test_spark_sql_literal_reuses_sql_parser(extract, rels, ents) -> None:
    res = extract([GLUE_ETL])
    transforms = ents(res, "data_transformation")
    assert transforms, "sql literal should mint a transformation"
    contains = rels(res, "CONTAINS")
    job = "aws-data:glue/jobs.orders_etl"
    assert any(
        frm == job and to.startswith("aws-data:glue/jobs/orders_etl.py@")
        for frm, to, _st in contains
    )
    writes = rels(res, "WRITES_TO")
    assert any(to == "aws-data:lake_db.agg_orders" for _f, to, _s in writes)


def test_dynamic_frame_markers_kind(extract, ents) -> None:
    res = extract([GLUE_ETL])
    kinds = {e.kind for e in res.entities}
    assert DomainEntityKind.GLUE_JOB in kinds
    assert DomainEntityKind.DATA_TABLE in kinds


def test_broken_py_degrades_without_crash(extract, diag_codes, ents) -> None:
    res = extract([BROKEN])
    assert "py-parse-error" in diag_codes(res)
    # scan degradado por tokenize ainda recupera literais s3/arn
    s3 = [e for e in ents(res, "data_table") if e.attributes.get("storage") == "s3-path"]
    assert s3 and s3[0].identity.qualified_name.startswith("s3://rescue-bucket")
    roles = ents(res, "iam_role")
    assert roles and roles[0].attributes.get("arn", "").endswith("role/RescueRole")


def test_same_table_different_accounts_stay_distinct(extract, ents) -> None:
    res = extract([CROSS_ACCOUNT])
    tables = [e for e in ents(res, "data_table") if e.name == "dim_customer"]
    # prod(111…), dev(222…), sem-catalog — três identidades distintas
    assert len(tables) == 3
    catalogs = {t.identity.catalog for t in tables}
    assert "aws:glue:111111111111" in catalogs
    assert "aws:glue:222222222222" in catalogs
    # sem catalog_id: serviço conhecido (glue API), conta ausente —
    # segmento parcial honesto, nunca funde com contas explícitas
    assert "aws:glue" in catalogs


def test_cross_account_reads_edges(extract, rels) -> None:
    res = extract([CROSS_ACCOUNT])
    reads = rels(res, "READS_FROM")
    job = "aws-data:glue/jobs.cross_account"
    assert (
        job,
        "aws-data:aws:glue:111111111111.shared_db.dim_customer",
        "resolved",
    ) in reads
    assert (
        job,
        "aws-data:aws:glue:222222222222.shared_db.dim_customer",
        "resolved",
    ) in reads
