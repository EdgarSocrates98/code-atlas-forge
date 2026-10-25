"""Delegação ``spark.sql(literal)`` → adapter ``sql`` (lf-0111).

O literal é parseado pelo parser ANSI/Spark já commitado; o resultado é
re-ancorado na transformação do call-site. Toda identidade emitida
carrega ``domain="pyspark"``, toda evidência aponta para o ``.py``.
"""

from __future__ import annotations

from code_atlas.domains.model import DomainEntityKind


class TestLiteralDelegation:
    def test_select_reads_tables(self, extract_inline, rels) -> None:
        res = extract_inline(
            "df = spark.sql(\n"
            "    'SELECT * FROM staging.a JOIN staging.b USING (id)'\n"
            ")\n"
        )
        reads = rels(res, "READS_FROM")
        assert (
            "pyspark:job.py#df@1", "pyspark:staging.a"
        ) in reads
        assert (
            "pyspark:job.py#df@1", "pyspark:staging.b"
        ) in reads

    def test_insert_read_write(self, extract_inline, rels) -> None:
        res = extract_inline(
            "spark.sql('INSERT INTO mart.d SELECT * FROM staging.s')\n"
        )
        assert (
            "pyspark:job.py#expr@1", "pyspark:staging.s"
        ) in rels(res, "READS_FROM")
        assert (
            "pyspark:job.py#expr@1", "pyspark:mart.d"
        ) in rels(res, "WRITES_TO")
        # collapse tabela→tabela (mesmo desenho do CTAS no adapter sql)
        assert (
            "pyspark:mart.d", "pyspark:staging.s"
        ) in rels(res, "DERIVES_FROM")

    def test_update_transforms(self, extract_inline, rels) -> None:
        res = extract_inline(
            "spark.sql('UPDATE mart.t SET a = 1 WHERE b > 0')\n"
        )
        assert (
            "pyspark:job.py#expr@1", "pyspark:mart.t"
        ) in rels(res, "TRANSFORMS")

    def test_cte_collapses(self, extract_inline, rels) -> None:
        res = extract_inline(
            "df = spark.sql(\n"
            "    'WITH x AS (SELECT * FROM staging.raw) '\n"
            "    'SELECT * FROM x'\n"
            ")\n"
        )
        reads = rels(res, "READS_FROM")
        # CTE ``x`` não vira tabela — a leitura colapsa na fonte real
        assert (
            "pyspark:job.py#df@1", "pyspark:staging.raw"
        ) in reads
        assert not any(
            t.endswith("x") and "staging.raw" not in t
            for _, t in reads
            if "CTE" in t
        )
        tables = {
            e.identity.qualified_name for e in res.entities
            if e.kind is DomainEntityKind.DATA_TABLE
        }
        assert "x" not in tables

    def test_malformed_literal_diag(self, extract_inline,
                                    diag_codes) -> None:
        res = extract_inline(
            "spark.sql('SELEC FRM nowhere')\n"
        )
        # diagnóstico do parser sql flui prefixado — nunca raise
        codes = diag_codes(res)
        assert codes, "expected sql parser diagnostics"
        assert all(
            "spark.sql@L" in d.message or d.code == "empty-sql"
            for d in res.diagnostics
        )

    def test_delegated_identities_are_pyspark(
        self, extract_inline
    ) -> None:
        res = extract_inline(
            "df = spark.sql('SELECT * FROM staging.t')\n"
        )
        for e in res.entities:
            assert e.identity.domain == "pyspark"
        for r in res.relations:
            if r.from_identity is not None:
                assert r.from_identity.domain == "pyspark"
            if r.to_identity is not None:
                assert r.to_identity.domain == "pyspark"

    def test_delegated_evidence_points_at_py_file(
        self, extract_inline
    ) -> None:
        res = extract_inline(
            "df = spark.sql('SELECT * FROM staging.t')\n"
        )
        for ev in res.evidence:
            assert ev.path == "job.py"
            assert ev.extractor == "domain:pyspark"

    def test_transformation_anchor_is_call_site(
        self, extract_inline
    ) -> None:
        res = extract_inline(
            "q = spark.sql('SELECT 1 FROM staging.t')\n"
        )
        ent = next(
            e for e in res.entities
            if e.kind is DomainEntityKind.DATA_TRANSFORMATION
        )
        assert ent.identity.qualified_name == "job.py#q@1"
        assert ent.attributes["operation"] == "sql"


class TestDynamicSql:
    def test_variable_arg(self, extract_inline, rels,
                          diag_codes) -> None:
        res = extract_inline(
            "def go(spark, q):\n"
            "    spark.sql(q)\n"
        )
        assert "dynamic-sql" in diag_codes(res)
        # nenhuma dependência de tabela fabricada
        assert rels(res, "READS_FROM") == set()
        pairs = rels(res, "DERIVES_FROM")
        assert ("pyspark:job.py#expr@2", "sql:q") in pairs

    def test_fstring_arg(self, extract_inline, rels,
                         diag_codes) -> None:
        res = extract_inline(
            "def go(spark, t):\n"
            "    spark.sql(f'SELECT * FROM {t}')\n"
        )
        assert "dynamic-sql" in diag_codes(res)
        assert rels(res, "READS_FROM") == set()

    def test_no_args(self, extract_inline, diag_codes) -> None:
        res = extract_inline("spark.sql()\n")
        assert "empty-sql" in diag_codes(res)
