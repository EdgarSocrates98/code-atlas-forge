"""Iceberg (``writeTo``/meta-tabelas/catálogo declarado) e Athena SQL."""

from __future__ import annotations

from conftest import ATHENA_RUNNER, ATHENA_SQL, ICEBERG


def test_declared_iceberg_catalog_entity(extract, ents) -> None:
    res = extract([ICEBERG])
    cats = ents(res, "data_catalog")
    lake = [c for c in cats if c.name == "lake"]
    assert lake
    assert lake[0].attributes.get("engine") == "iceberg"
    assert "iceberg" in lake[0].attributes.get("catalog_impl", "")


def test_write_to_emits_iceberg_table(extract, rels, ents) -> None:
    res = extract([ICEBERG])
    ice = ents(res, "iceberg_table")
    names = {e.identity.qualified_name for e in ice}
    assert "orders" in names  # lake.db.orders
    writes = rels(res, "WRITES_TO")
    assert (
        "aws-data:glue/jobs.iceberg_upsert",
        "aws-data:lake.db.orders",
        "resolved",
    ) in writes


def test_catalog_warehouse_reference(extract, rels) -> None:
    res = extract([ICEBERG])
    refs = rels(res, "REFERENCES")
    assert ("aws-data:lake", "aws-data:s3://lake-wh/warehouse", "resolved") in refs


def test_meta_table_snapshots_is_iceberg(extract, ents, rels) -> None:
    res = extract([ICEBERG])
    meta = [
        e
        for e in ents(res, "iceberg_table")
        if e.attributes.get("iceberg_meta_table") == "snapshots"
    ]
    assert meta
    reads = rels(res, "READS_FROM")
    assert any(to == "aws-data:lake.db.orders.snapshots" for _f, to, _s in reads)


def test_merge_and_insert_literals_parsed(extract, ents, rels) -> None:
    res = extract([ICEBERG])
    transforms = ents(res, "data_transformation")
    kinds = {t.attributes.get("statement") for t in transforms}
    assert "merge" in kinds or "insert" in kinds
    # INSERT INTO lake.db.orders_archive — alvo iceberg via catálogo
    writes = rels(res, "WRITES_TO")
    assert any(to == "aws-data:lake.db.orders_archive" for _f, to, _s in writes)


def test_s3_read_path(extract, rels) -> None:
    res = extract([ICEBERG])
    reads = rels(res, "READS_FROM")
    assert (
        "aws-data:glue/jobs.iceberg_upsert",
        "aws-data:s3://inbound-bucket/orders",
        "resolved",
    ) in reads


# -- Athena .sql + boto3 -------------------------------------------------


def test_athena_sql_file_ctas_and_s3(extract, ents, rels) -> None:
    res = extract([ATHENA_SQL])
    tables = {e.name for e in ents(res, "data_table")}
    assert "raw_events" in tables
    s3 = [e for e in ents(res, "data_table") if e.attributes.get("storage") == "s3-path"]
    assert s3 and s3[0].identity.qualified_name == ("s3://events-bucket/raw")
    refs = rels(res, "REFERENCES")
    assert any(to == "aws-data:s3://events-bucket/raw" for _f, to, _s in refs)
    derives = rels(res, "DERIVES_FROM")
    assert any(to == "aws-data:staging.raw_events" for _f, to, _s in derives)


def test_athena_runner_querystring_and_output(extract, rels, ents) -> None:
    res = extract([ATHENA_RUNNER])
    # QueryString → transformação via reuso do parser sql
    transforms = ents(res, "data_transformation")
    assert transforms
    writes = rels(res, "WRITES_TO")
    # contexto declarado: Catalog=AwsDataCatalog/Database=lake_db →
    # USE sintético; alvo 2-part carrega o catálogo explicitamente
    # (identificador não-quotado normaliza para lowercase no parser sql)
    assert any(to == "aws-data:awsdatacatalog.reporting.daily_totals" for _f, to, _s in writes)
    # OutputLocation literal → escrita no path s3
    assert any(to == "aws-data:s3://athena-results-bucket/daily" for _f, to, _s in writes)


def test_create_job_emits_glue_job_and_assumes_role(extract, ents, rels) -> None:
    res = extract([ATHENA_RUNNER])
    jobs = [e for e in ents(res, "glue_job") if e.name == "nightly-enrich"]
    assert jobs
    roles = ents(res, "iam_role")
    assert any(
        r.attributes.get("arn") == "arn:aws:iam::123456789012:role/GlueNightlyRole" for r in roles
    )
    assumes = rels(res, "ASSUMES_ROLE")
    assert (
        "aws-data:glue/jobs.nightly-enrich",
        "aws-data:aws:iam:123456789012.role/GlueNightlyRole",
        "external",
    ) in assumes
