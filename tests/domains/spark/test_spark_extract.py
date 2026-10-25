"""Extração inline — cada superfície reconhecida do ``domains.spark``.

Cobre read → transform → write, joins, famílias de write, temp views,
sessões implícitas, propagação de literal ``str``, receiver
não-resolvível e arquivos não-Spark/malformados.
"""

from __future__ import annotations

from code_atlas.domains.model import DomainEntityKind


def _names(res, kind: DomainEntityKind) -> set[str]:
    return {
        e.identity.display()
        for e in res.entities
        if e.kind is kind
    }


class TestReadWrite:
    def test_catalog_table_read(self, extract_inline, rels) -> None:
        res = extract_inline(
            "df = spark.read.table('staging.orders')\n"
        )
        assert ("pyspark:job.py#df@1", "pyspark:staging.orders") in rels(
            res, "READS_FROM"
        )
        t = next(
            e for e in res.entities
            if e.identity.qualified_name == "orders"
        )
        assert t.kind is DomainEntityKind.DATA_TABLE
        assert t.identity.namespace == "staging"

    def test_path_read_is_not_a_table(self, extract_inline, rels) -> None:
        res = extract_inline(
            "df = spark.read.parquet('s3://bkt/dir/')\n"
        )
        assert (
            "pyspark:job.py#df@1",
            "pyspark:s3://bkt/dir/",
        ) in rels(res, "READS_FROM")
        ent = next(
            e for e in res.entities
            if e.identity.qualified_name == "s3://bkt/dir/"
        )
        assert ent.attributes["storage"] == "path"
        assert ent.attributes["uri_scheme"] == "s3"
        # path NUNCA recebe namespace de catálogo
        assert ent.identity.namespace == ""
        assert ent.identity.catalog == ""

    def test_read_format_load(self, extract_inline, rels) -> None:
        res = extract_inline(
            "df = spark.read.format('delta').load('s3://bkt/x/')\n"
        )
        assert (
            "pyspark:job.py#df@1",
            "pyspark:s3://bkt/x/",
        ) in rels(res, "READS_FROM")
        ent = next(
            e for e in res.entities
            if e.identity.qualified_name == "s3://bkt/x/"
        )
        assert ent.attributes["format"] == "delta"

    def test_read_jdbc_catalog(self, extract_inline, rels) -> None:
        res = extract_inline(
            "df = spark.read.jdbc('jdbc:pg://h/db', 'crm.accounts')\n"
        )
        assert (
            "pyspark:job.py#df@1",
            "pyspark:crm.accounts",
        ) in rels(res, "READS_FROM")

    def test_spark_table_call(self, extract_inline, rels) -> None:
        res = extract_inline("df = spark.table('staging.t')\n")
        assert (
            "pyspark:job.py#df@1", "pyspark:staging.t"
        ) in rels(res, "READS_FROM")

    def test_builder_getorcreate(self, extract_inline, rels) -> None:
        res = extract_inline(
            "from pyspark.sql import SparkSession\n"
            "spark = SparkSession.builder.appName('x').getOrCreate()\n"
            "df = spark.read.table('staging.t')\n"
        )
        assert (
            "pyspark:job.py#df@3", "pyspark:staging.t"
        ) in rels(res, "READS_FROM")

    def test_explicit_session_name(self, extract_inline, rels) -> None:
        res = extract_inline(
            "from pyspark.sql import SparkSession\n"
            "sess = SparkSession.builder.getOrCreate()\n"
            "df = sess.read.parquet('s3://bkt/p/')\n"
        )
        assert (
            "pyspark:job.py#df@3",
            "pyspark:s3://bkt/p/",
        ) in rels(res, "READS_FROM")


class TestTransformChains:
    def test_var_chain_join(self, extract_inline, rels) -> None:
        res = extract_inline(
            "a = spark.read.table('staging.a')\n"
            "b = spark.read.table('staging.b')\n"
            "c = a.join(b, a.id == b.id, 'left')\n"
        )
        pairs = rels(res, "DERIVES_FROM")
        assert ("pyspark:job.py#c@3", "pyspark:job.py#a@1") in pairs
        assert ("pyspark:job.py#c@3", "pyspark:job.py#b@2") in pairs

    def test_full_chain_write_collapse(
        self, extract_inline, rels
    ) -> None:
        res = extract_inline(
            "o = spark.read.table('staging.orders')\n"
            "c = spark.read.parquet('s3://bkt/cust/')\n"
            "e = o.join(c, 'id').dropDuplicates(['id'])\n"
            "e.write.saveAsTable('mart.enriched')\n"
        )
        # collapse sink → fontes terminais (mesmo desenho do CTAS sql)
        pairs = rels(res, "DERIVES_FROM")
        assert (
            "pyspark:mart.enriched", "pyspark:staging.orders"
        ) in pairs
        assert (
            "pyspark:mart.enriched", "pyspark:s3://bkt/cust/"
        ) in pairs
        assert (
            "pyspark:job.py#e@3", "pyspark:mart.enriched"
        ) in rels(res, "WRITES_TO")

    def test_transform_family(self, extract_inline, rels) -> None:
        res = extract_inline(
            "d = spark.read.table('staging.t')\n"
            "d2 = (d.select('a').filter('x').groupBy('g')\n"
            "      .agg({'v': 'sum'}).withColumn('w', 'v * 2'))\n"
        )
        assert (
            "pyspark:job.py#d2@2", "pyspark:job.py#d@1"
        ) in rels(res, "DERIVES_FROM")

    def test_alias_rebind_keeps_identity(
        self, extract_inline, rels
    ) -> None:
        res = extract_inline(
            "a = spark.read.table('staging.t')\n"
            "b = a\n"
            "b.write.saveAsTable('mart.out')\n"
        )
        assert (
            "pyspark:job.py#a@1", "pyspark:mart.out"
        ) in rels(res, "WRITES_TO")

    def test_union_two_parents(self, extract_inline, rels) -> None:
        res = extract_inline(
            "a = spark.read.table('staging.a')\n"
            "b = spark.read.table('staging.b')\n"
            "u = a.union(b)\n"
        )
        pairs = rels(res, "DERIVES_FROM")
        assert ("pyspark:job.py#u@3", "pyspark:job.py#a@1") in pairs
        assert ("pyspark:job.py#u@3", "pyspark:job.py#b@2") in pairs


class TestWrites:
    def test_save_as_table(self, extract_inline, rels) -> None:
        res = extract_inline(
            "d = spark.read.table('staging.t')\n"
            "d.write.saveAsTable('mart.out')\n"
        )
        assert (
            "pyspark:job.py#d@1", "pyspark:mart.out"
        ) in rels(res, "WRITES_TO")

    def test_insert_into(self, extract_inline, rels) -> None:
        res = extract_inline(
            "d = spark.read.table('staging.t')\n"
            "d.write.insertInto('staging.t')\n"
        )
        assert (
            "pyspark:job.py#d@1", "pyspark:staging.t"
        ) in rels(res, "WRITES_TO")
        # auto-derivação suprimida — nunca aresta t→t
        assert (
            "pyspark:staging.t", "pyspark:staging.t"
        ) not in rels(res, "DERIVES_FROM")

    def test_write_to_iceberg(self, extract_inline, rels) -> None:
        res = extract_inline(
            "d = spark.read.table('staging.t')\n"
            "d.writeTo('lake.t').using('iceberg').createOrReplace()\n"
        )
        ent = next(
            e for e in res.entities
            if e.identity.qualified_name == "t"
            and e.identity.namespace == "lake"
        )
        assert ent.kind is DomainEntityKind.ICEBERG_TABLE
        assert ent.attributes["using"] == "iceberg"
        assert (
            "pyspark:job.py#d@1", "pyspark:lake.t"
        ) in rels(res, "WRITES_TO")
        assert (
            "pyspark:lake.t", "pyspark:staging.t"
        ) in rels(res, "DERIVES_FROM")

    def test_write_mode_recorded(self, extract_inline) -> None:
        res = extract_inline(
            "d = spark.read.table('staging.t')\n"
            "d.write.mode('append').saveAsTable('mart.out')\n"
        )
        ent = next(
            e for e in res.entities
            if e.identity.qualified_name == "out"
        )
        assert ent.attributes["write_mode"] == "append"

    def test_path_write(self, extract_inline, rels) -> None:
        res = extract_inline(
            "d = spark.read.table('staging.t')\n"
            "d.write.parquet('s3://bkt/out/')\n"
        )
        assert (
            "pyspark:job.py#d@1", "pyspark:s3://bkt/out/"
        ) in rels(res, "WRITES_TO")
        assert (
            "pyspark:s3://bkt/out/", "pyspark:staging.t"
        ) in rels(res, "DERIVES_FROM")

    def test_dynamic_write_target_unresolved(
        self, extract_inline, rels, diag_codes
    ) -> None:
        res = extract_inline(
            "def go(d, target):\n"
            "    d.write.saveAsTable(target)\n"
        )
        writes = rels(res, "WRITES_TO")
        assert any(t == "target" for _, t in writes), writes
        assert "dynamic-write-target" in diag_codes(res)
        # nenhuma tabela fabricada com o texto do parâmetro
        assert not any(
            e.identity.qualified_name == "target"
            for e in res.entities
        )


class TestHonesty:
    def test_dynamic_sql_diag_no_fabrication(
        self, extract_inline, rels, diag_codes
    ) -> None:
        res = extract_inline(
            "def go(spark, q):\n"
            "    spark.sql(q)\n"
        )
        assert "dynamic-sql" in diag_codes(res)
        assert _names(res, DomainEntityKind.DATA_TABLE) == set()
        pairs = rels(res, "DERIVES_FROM")
        assert any(t == "sql:q" for _, t in pairs), pairs

    def test_fstring_sql_diag(self, extract_inline, diag_codes) -> None:
        res = extract_inline(
            "def go(spark, t):\n"
            "    spark.sql(f'select * from {t}')\n"
        )
        assert "dynamic-sql" in diag_codes(res)
        assert _names(res, DomainEntityKind.DATA_TABLE) == set()

    def test_dynamic_source_diag(self, extract_inline, rels,
                                 diag_codes) -> None:
        res = extract_inline(
            "def go(spark, p):\n"
            "    df = spark.read.parquet(p)\n"
        )
        assert "dynamic-source" in diag_codes(res)
        pairs = rels(res, "DERIVES_FROM")
        assert any(t == "p" for _, t in pairs), pairs
        assert _names(res, DomainEntityKind.DATA_TABLE) == set()

    def test_unresolved_receiver_partial(
        self, extract_inline, rels, diag_codes
    ) -> None:
        res = extract_inline(
            "def go():\n"
            "    src = get_source()\n"
            "    out = src.filter('x > 0')\n"
            "    out.write.saveAsTable('mart.out')\n"
        )
        assert "unresolved-dataframe" in diag_codes(res)
        pairs = rels(res, "DERIVES_FROM")
        assert ("pyspark:job.py#out@3", "src") in pairs
        # o write ainda registra — linhagem parcial, não vazia
        assert (
            "pyspark:job.py#out@3", "pyspark:mart.out"
        ) in rels(res, "WRITES_TO")
        ent = next(
            e for e in res.entities
            if e.identity.display() == "pyspark:job.py#out@3"
        )
        assert ent.attributes["lineage"] == "partial"

    def test_malformed_python_diag_not_raise(
        self, extract_inline, diag_codes
    ) -> None:
        res = extract_inline("df = spark.read.table(\n")
        assert "parse-error" in diag_codes(res)
        assert res.entities == ()

    def test_non_spark_file_silent(
        self, extract_inline, diag_codes
    ) -> None:
        res = extract_inline(
            "import os\n\n"
            "def pwd():\n"
            "    return os.getcwd()\n"
        )
        assert res.entities == ()
        assert res.relations == ()
        assert res.diagnostics == ()
        assert res.stats.get("non_spark_files") == 1

    def test_string_constant_propagates(
        self, extract_inline, rels
    ) -> None:
        res = extract_inline(
            "TABLE = 'staging.orders'\n"
            "df = spark.read.table(TABLE)\n"
        )
        assert (
            "pyspark:job.py#df@2", "pyspark:staging.orders"
        ) in rels(res, "READS_FROM")

    def test_unresolved_text_has_resolution_record(
        self, extract_inline
    ) -> None:
        res = extract_inline(
            "def go(spark, q):\n"
            "    spark.sql(q)\n"
        )
        unres = [
            r for r in res.relations if r.to_unresolved
        ]
        assert unres
        for r in unres:
            assert r.resolution is not None
            assert r.resolution.status.value == "unresolved"
            assert r.resolution.unresolved_text == r.to_unresolved

    def test_unknown_df_method_does_not_fabricate(
        self, extract_inline, rels
    ) -> None:
        res = extract_inline(
            "d = spark.read.table('staging.t')\n"
            "e = d.mysteryOp('x')\n"
            "e.write.saveAsTable('mart.out')\n"
        )
        # método desconhecido → receiver vira unknown → sem aresta fake
        writes = rels(res, "WRITES_TO")
        assert writes, "expected write still recorded"
        assert any(t == "pyspark:mart.out" for _, t in writes)
        frm = next(iter(writes))[0]
        assert frm != "pyspark:job.py#d@1"  # honesta: sem salto de linhagem


class TestSparkJob:
    def test_job_entity_per_spark_file(self, extract_inline) -> None:
        res = extract_inline(
            "df = spark.read.table('staging.t')\n"
        )
        jobs = _names(res, DomainEntityKind.SPARK_JOB)
        assert jobs == {"pyspark:job"}
        job = next(
            e for e in res.entities
            if e.kind is DomainEntityKind.SPARK_JOB
        )
        assert job.attributes["entry_point"] == "job.py"
        assert job.attributes["language"] == "python"
        assert job.path == "job.py"

    def test_app_name_captured(self, extract_inline) -> None:
        res = extract_inline(
            "from pyspark.sql import SparkSession\n"
            "spark = (SparkSession.builder.appName('daily-etl')\n"
            "        .getOrCreate())\n"
            "df = spark.read.table('staging.t')\n"
        )
        job = next(
            e for e in res.entities
            if e.kind is DomainEntityKind.SPARK_JOB
        )
        assert job.attributes["app_name"] == "daily-etl"

    def test_job_contains_transformations(
        self, extract_inline, rels
    ) -> None:
        res = extract_inline(
            "a = spark.read.table('staging.a')\n"
            "b = a.filter('x')\n"
            "b.write.saveAsTable('mart.b')\n"
        )
        contains = rels(res, "CONTAINS")
        assert (
            "pyspark:job", "pyspark:job.py#a@1"
        ) in contains
        assert (
            "pyspark:job", "pyspark:job.py#b@2"
        ) in contains

    def test_no_job_on_non_spark_file(self, extract_inline) -> None:
        res = extract_inline("x = 1\n")
        assert _names(res, DomainEntityKind.SPARK_JOB) == set()


class TestImplicitAndScoping:
    def test_implicit_session_notebook(
        self, extract_inline, rels
    ) -> None:
        res = extract_inline(
            "df = spark.read.table('staging.events')\n"
            "df.write.insertInto('staging.out')\n"
        )
        assert (
            "pyspark:job.py#df@1", "pyspark:staging.events"
        ) in rels(res, "READS_FROM")
        assert (
            "pyspark:job.py#df@1", "pyspark:staging.out"
        ) in rels(res, "WRITES_TO")

    def test_function_scoped_env(self, extract_inline, rels) -> None:
        """``df`` reutilizado em funções distintas não funde cadeias."""
        res = extract_inline(
            "def a(spark):\n"
            "    df = spark.read.table('staging.one')\n"
            "    return df\n"
            "def b(spark):\n"
            "    df = spark.read.table('staging.two')\n"
            "    return df\n",
            name="two_jobs.py",
        )
        reads = rels(res, "READS_FROM")
        assert ("pyspark:two_jobs.py#df@2", "pyspark:staging.one") in reads
        assert ("pyspark:two_jobs.py#df@5", "pyspark:staging.two") in reads
        assert (
            "pyspark:two_jobs.py#df@2", "pyspark:staging.two"
        ) not in reads

    def test_self_spark_attribute(self, extract_inline, rels) -> None:
        res = extract_inline(
            "class Job:\n"
            "    def run(self):\n"
            "        df = self.spark.read.table('staging.t')\n"
            "        df.write.saveAsTable('mart.t')\n"
        )
        assert (
            "pyspark:job.py#df@3", "pyspark:staging.t"
        ) in rels(res, "READS_FROM")


class TestTempViews:
    def test_temp_view_registers_and_reads(
        self, extract_inline, rels
    ) -> None:
        res = extract_inline(
            "o = spark.read.table('staging.orders')\n"
            "o.createOrReplaceTempView('orders_v')\n"
            "j = spark.sql('SELECT * FROM orders_v')\n"
            "j.write.saveAsTable('mart.out')\n"
        )
        views = _names(res, DomainEntityKind.DATA_VIEW)
        assert "pyspark:orders_v" in views
        # read pelo alias — aresta honesta na view
        assert (
            "pyspark:job.py#j@3", "pyspark:orders_v"
        ) in rels(res, "READS_FROM")
        # colapso do write salta a view para a fonte terminal
        assert (
            "pyspark:mart.out", "pyspark:staging.orders"
        ) in rels(res, "DERIVES_FROM")

    def test_if_branch_merge(self, extract_inline, rels) -> None:
        res = extract_inline(
            "if mode == 'full':\n"
            "    df = spark.read.table('staging.full_src')\n"
            "else:\n"
            "    df = spark.read.table('staging.delta_src')\n"
            "df.write.saveAsTable('mart.out')\n"
        )
        derived = rels(res, "DERIVES_FROM")
        # merge honesto: mart.out colapsa nas DUAS alternativas
        assert (
            "pyspark:mart.out", "pyspark:staging.full_src"
        ) in derived
        assert (
            "pyspark:mart.out", "pyspark:staging.delta_src"
        ) in derived
