"""Repo real ``sql_warehouse``: landing → staging → mart ponta a ponta.

O pipeline é deliberadamente heterogêneo: DDL com ``STORED AS PARQUET``
(Spark), ``CREATE VIEW`` com join (staging), ``INSERT … WITH`` com CTE,
CTAS agregado e ``MERGE`` (mart). Cada camada deve ler a anterior com
identidades schema-qualificadas e linhagem de coluna honesta.
"""

from __future__ import annotations

from conftest import BROKEN_PATH, PIPELINE_PATHS, make_request

from code_atlas.domains.contracts import DomainExtractionResult


def _edges(res: DomainExtractionResult, rtype: str) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for r in res.relations:
        if r.relation_type != rtype:
            continue
        f = r.from_identity.display() if r.from_identity else "?"
        t = (
            r.to_identity.display()
            if r.to_identity
            else r.to_unresolved or "?"
        )
        out.add((f, t))
    return out


def _tables(res: DomainExtractionResult) -> set[str]:
    return {
        e.identity.display()
        for e in res.entities
        if e.kind.value in ("data_table", "data_view")
    }


def test_layer_entities_exist(
    full_result: DomainExtractionResult,
) -> None:
    assert _tables(full_result) == {
        "sql:landing.orders",
        "sql:landing.customers",
        "sql:staging.v_enriched_orders",
        "sql:staging.order_facts",
        "sql:mart.daily_revenue",
        "sql:mart.customer_stats",
    }


def test_view_is_typed_as_view(
    full_result: DomainExtractionResult,
) -> None:
    kinds = {
        e.identity.display(): e.kind.value
        for e in full_result.entities
        if e.kind.value in ("data_table", "data_view")
    }
    assert kinds["sql:staging.v_enriched_orders"] == "data_view"
    assert kinds["sql:landing.orders"] == "data_table"


def test_staging_view_reads_landing(
    full_result: DomainExtractionResult,
) -> None:
    assert _edges(full_result, "DERIVES_FROM") >= {
        ("sql:staging.v_enriched_orders", "sql:landing.orders"),
        ("sql:staging.v_enriched_orders", "sql:landing.customers"),
    }


def test_staging_facts_read_view(
    full_result: DomainExtractionResult,
) -> None:
    assert (
        "sql:staging.order_facts", "sql:staging.v_enriched_orders"
    ) in _edges(full_result, "DERIVES_FROM")


def test_mart_reads_staging(
    full_result: DomainExtractionResult,
) -> None:
    assert _edges(full_result, "DERIVES_FROM") >= {
        ("sql:mart.daily_revenue", "sql:staging.order_facts"),
        ("sql:mart.customer_stats", "sql:staging.order_facts"),
    }


def test_end_to_end_column_lineage(
    full_result: DomainExtractionResult,
) -> None:
    d = _edges(full_result, "DERIVES_FROM")
    # staging: renomeado o.amount → amount_usd na view
    assert (
        "sql:staging.v_enriched_orders.amount_usd",
        "sql:landing.orders.amount",
    ) in d
    assert (
        "sql:staging.v_enriched_orders.region",
        "sql:landing.customers.region",
    ) in d
    # staging facts: CTE transparente até a view
    assert (
        "sql:staging.order_facts.amount_usd",
        "sql:staging.v_enriched_orders.amount_usd",
    ) in d
    # mart: agregação — SUM(amount_usd)/COUNT(order_id) viram arestas
    assert (
        "sql:mart.daily_revenue.revenue",
        "sql:staging.order_facts.amount_usd",
    ) in d
    assert (
        "sql:mart.daily_revenue.orders",
        "sql:staging.order_facts.order_id",
    ) in d
    assert (
        "sql:mart.customer_stats.total_spend",
        "sql:staging.order_facts.amount_usd",
    ) in d


def test_pipeline_is_diagnostic_free(
    full_result: DomainExtractionResult,
) -> None:
    assert [
        (d.severity, d.code, d.path) for d in full_result.diagnostics
    ] == []


def test_broken_file_partial_plus_diagnostics(
    adapter,
) -> None:
    res = adapter.extract(
        make_request(paths=(*PIPELINE_PATHS, BROKEN_PATH))
    )
    codes = [d.code for d in res.diagnostics]
    assert "parse-error" in codes
    # os statements válidos do arquivo quebrado ainda extraíram:
    # `SELECT recovered FROM staging.order_facts` e o tail `landing.orders`
    reads = _edges(res, "READS_FROM")
    assert any(
        tgt == "sql:staging.order_facts" and src.startswith(
            "sql:broken/99_broken.sql#"
        )
        for src, tgt in reads
    )
    assert any(
        tgt == "sql:landing.orders" and src.startswith(
            "sql:broken/99_broken.sql#"
        )
        for src, tgt in reads
    )
    # o resto do pipeline continua intacto — isolamento por arquivo
    assert (
        "sql:mart.daily_revenue", "sql:staging.order_facts"
    ) in _edges(res, "DERIVES_FROM")


def test_identities_are_schema_qualified(
    full_result: DomainExtractionResult,
) -> None:
    by_name = {e.identity.display(): e.identity for e in full_result.entities}
    o = by_name["sql:landing.orders"]
    assert o.namespace == "landing" and o.qualified_name == "orders"
    v = by_name["sql:staging.v_enriched_orders"]
    assert v.namespace == "staging"
