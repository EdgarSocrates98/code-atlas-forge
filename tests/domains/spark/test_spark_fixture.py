"""Testes sobre a fixture real ``tests/fixtures/pyspark_repo/``.

O mini-repo cobre: pipeline ETL completo, job centrado em ``spark.sql``,
job dinâmico, arquivo não-Spark, arquivo malformado e notebook de
sessão implícita.
"""

from __future__ import annotations

from conftest import ALL_PATHS

from code_atlas.domains.contracts import DomainExtractionResult
from code_atlas.domains.model import DomainEntityKind


def _names(res, kind: DomainEntityKind) -> set[str]:
    return {
        e.identity.display()
        for e in res.entities
        if e.kind is kind
    }


class TestEtlPipeline:
    def test_staging_reads(self, full_result, rels) -> None:
        reads = rels(full_result, "READS_FROM")
        assert (
            "pyspark:etl_orders.py#orders@18",
            "pyspark:staging.orders",
        ) in reads
        assert (
            "pyspark:etl_orders.py#customers@19",
            "pyspark:s3://datalake/customers/",
        ) in reads
        assert (
            "pyspark:etl_orders.py#archive@20",
            "pyspark:s3://datalake/customers_archive/",
        ) in reads

    def test_join_chain(self, full_result, rels) -> None:
        derived = rels(full_result, "DERIVES_FROM")
        assert (
            "pyspark:etl_orders.py#all_customers@22",
            "pyspark:etl_orders.py#customers@19",
        ) in derived
        assert (
            "pyspark:etl_orders.py#all_customers@22",
            "pyspark:etl_orders.py#archive@20",
        ) in derived
        assert (
            "pyspark:etl_orders.py#enriched@24",
            "pyspark:etl_orders.py#orders@18",
        ) in derived
        assert (
            "pyspark:etl_orders.py#enriched@24",
            "pyspark:etl_orders.py#all_customers@22",
        ) in derived

    def test_save_as_table_write_collapse(
        self, full_result, rels
    ) -> None:
        writes = rels(full_result, "WRITES_TO")
        assert (
            "pyspark:etl_orders.py#enriched@24",
            "pyspark:mart.enriched_orders",
        ) in writes
        derived = rels(full_result, "DERIVES_FROM")
        for src in (
            "pyspark:staging.orders",
            "pyspark:s3://datalake/customers/",
            "pyspark:s3://datalake/customers_archive/",
        ):
            assert (
                "pyspark:mart.enriched_orders", src
            ) in derived, src

    def test_path_write(self, full_result, rels) -> None:
        writes = rels(full_result, "WRITES_TO")
        assert (
            "pyspark:etl_orders.py#enriched@24",
            "pyspark:s3://datalake/mart/enriched_orders/",
        ) in writes
        ent = next(
            e for e in full_result.entities
            if e.identity.qualified_name
            == "s3://datalake/mart/enriched_orders/"
        )
        assert ent.attributes["storage"] == "path"
        assert ent.attributes["roles"] == "write"

    def test_write_mode_recorded(self, full_result) -> None:
        ent = next(
            e for e in full_result.entities
            if e.identity.qualified_name == "enriched_orders"
        )
        assert ent.attributes["write_mode"] == "overwrite"


class TestSqlJob:
    def test_literal_select_reads(self, full_result, rels) -> None:
        reads = rels(full_result, "READS_FROM")
        assert (
            "pyspark:sql_job.py#region_counts@11",
            "pyspark:staging.customers",
        ) in reads
        assert (
            "pyspark:sql_job.py#region_counts@11",
            "pyspark:staging.regions",
        ) in reads

    def test_insert_literal_write(self, full_result, rels) -> None:
        writes = rels(full_result, "WRITES_TO")
        assert (
            "pyspark:sql_job.py#expr@18",
            "pyspark:mart.daily",
        ) in writes
        reads = rels(full_result, "READS_FROM")
        assert (
            "pyspark:sql_job.py#expr@18",
            "pyspark:staging.daily_source",
        ) in reads
        derived = rels(full_result, "DERIVES_FROM")
        assert (
            "pyspark:mart.daily",
            "pyspark:staging.daily_source",
        ) in derived

    def test_write_to_iceberg(self, full_result, rels) -> None:
        ices = _names(full_result, DomainEntityKind.ICEBERG_TABLE)
        assert "pyspark:mart.customer_region_counts" in ices
        writes = rels(full_result, "WRITES_TO")
        assert (
            "pyspark:sql_job.py#region_counts@11",
            "pyspark:mart.customer_region_counts",
        ) in writes
        derived = rels(full_result, "DERIVES_FROM")
        for src in (
            "pyspark:staging.customers",
            "pyspark:staging.regions",
        ):
            assert (
                "pyspark:mart.customer_region_counts", src
            ) in derived, src

    def test_select_star_literal_diagnosed(
        self, full_result, diag_codes
    ) -> None:
        assert "select-star" in diag_codes(full_result)


class TestDynamicJob:
    def test_dynamic_diagnostics(self, full_result, diag_codes) -> None:
        codes = diag_codes(full_result)
        assert "dynamic-sql" in codes
        assert "dynamic-source" in codes
        assert "dynamic-write-target" in codes
        assert "unresolved-dataframe" in codes

    def test_no_fabricated_targets(self, full_result) -> None:
        for e in full_result.entities:
            qname = e.identity.qualified_name
            assert "table_name" not in qname
            assert "out_path" not in qname
            assert "query" not in qname

    def test_partial_lineage_kept(self, full_result, rels) -> None:
        writes = rels(full_result, "WRITES_TO")
        # receiver não-resolvível ainda escreve — aresta honesta
        assert (
            "pyspark:dynamic_job.py#expr@29",
            "pyspark:staging.incoming_clean",
        ) in writes
        derived = rels(full_result, "DERIVES_FROM")
        assert (
            "pyspark:dynamic_job.py#expr@29", "src"
        ) in derived


class TestNotebookImplicit:
    def test_implicit_session_extracts(self, full_result, rels) -> None:
        reads = rels(full_result, "READS_FROM")
        assert (
            "pyspark:notebook_implicit.py#df@8",
            "pyspark:staging.events",
        ) in reads
        writes = rels(full_result, "WRITES_TO")
        assert (
            "pyspark:notebook_implicit.py#clean@10",
            "pyspark:staging.events_clean",
        ) in writes


class TestViewsJob:
    def test_temp_view_entity(self, full_result) -> None:
        views = _names(full_result, DomainEntityKind.DATA_VIEW)
        assert "pyspark:orders_v" in views
        ent = next(
            e for e in full_result.entities
            if e.identity.qualified_name == "orders_v"
        )
        assert ent.attributes["temporary"] == "true"

    def test_view_read_edge_and_collapse(
        self, full_result, rels
    ) -> None:
        reads = rels(full_result, "READS_FROM")
        assert (
            "pyspark:views_job.py#joined@15",
            "pyspark:orders_v",
        ) in reads
        assert (
            "pyspark:views_job.py#joined@15",
            "pyspark:staging.customers",
        ) in reads
        derived = rels(full_result, "DERIVES_FROM")
        # mart.joined colapsa nas fontes terminais — a view é alias
        assert (
            "pyspark:mart.joined", "pyspark:staging.orders"
        ) in derived
        assert (
            "pyspark:mart.joined", "pyspark:staging.customers"
        ) in derived


class TestNegativeCases:
    def test_broken_file_parse_error(
        self, full_result, diag_codes
    ) -> None:
        assert "parse-error" in diag_codes(full_result)
        bad = [
            d for d in full_result.diagnostics
            if d.path == "broken_job.py"
        ]
        assert bad and bad[0].severity == "error"

    def test_plain_file_no_claims(self, full_result) -> None:
        plain = [
            e for e in full_result.entities
            if e.path == "plain_utils.py"
        ]
        assert plain == []
        plain_rels = [
            r for r in full_result.relations
            if r.path == "plain_utils.py"
        ]
        assert plain_rels == []
        assert full_result.stats.get("non_spark_files") == 1

    def test_stats(self, full_result) -> None:
        assert full_result.stats["files_seen"] == len(ALL_PATHS)
        assert full_result.stats["spark_files"] == 5
        assert full_result.stats["parse_errors"] == 1
        assert full_result.stats["sql_literals"] == 3
        assert full_result.stats["sql_dynamic"] == 2

    def test_entities_have_artifact_path(
        self, full_result: DomainExtractionResult
    ) -> None:
        for e in full_result.entities:
            assert e.path.endswith(".py"), e.identity.display()
