"""Extração de domínio — entidades, relações e estados honestos."""

from __future__ import annotations


def test_create_table_declares_columns(inline) -> None:
    fx = inline("CREATE TABLE s.t (a INT, b STRING);")
    kinds = {e.identity.display(): e.kind.value for e in fx.entities}
    assert kinds["sql:s.t"] == "data_table"
    assert kinds["sql:s.t.a"] == "data_column"
    assert kinds["sql:s.t.b"] == "data_column"
    ent = next(e for e in fx.entities if e.name == "a")
    assert ent.attributes["declared_type"] == "INT"


def test_insert_produces_reads_writes_derives(inline, rels) -> None:
    fx = inline("INSERT INTO m.t SELECT a FROM s.x;")
    assert rels(fx, "READS_FROM") == {("sql:inline.sql#0", "sql:s.x")}
    assert rels(fx, "WRITES_TO") == {("sql:inline.sql#0", "sql:m.t")}
    assert rels(fx, "DERIVES_FROM") == {("sql:m.t", "sql:s.x")}


def test_ctas_table_lineage(inline, rels) -> None:
    fx = inline(
        "CREATE TABLE m.out AS SELECT x, y FROM a.b JOIN c.d "
        "ON b.k = d.k;"
    )
    assert rels(fx, "DERIVES_FROM") == {
        ("sql:m.out", "sql:a.b"), ("sql:m.out", "sql:c.d"),
    }
    assert rels(fx, "WRITES_TO") == {("sql:inline.sql#0", "sql:m.out")}
    ent = next(e for e in fx.entities if e.name == "out")
    assert ent.attributes["declared"] == "create"


def test_nested_cte_collapses_to_base_tables(inline, rels) -> None:
    fx = inline(
        "WITH a AS (SELECT x FROM t1), "
        "b AS (SELECT x FROM a JOIN t2 ON a.k = t2.k) "
        "INSERT INTO m.t SELECT * FROM b;"
    )
    # CTEs são transparentes: leitura liga às tabelas base
    assert rels(fx, "READS_FROM") == {
        ("sql:inline.sql#0", "sql:t1"), ("sql:inline.sql#0", "sql:t2"),
    }
    names = {e.name for e in fx.entities}
    assert "a" not in names and "b" not in names  # CTE não vira entidade


def test_subquery_collapses_to_base_table(inline, rels) -> None:
    fx = inline(
        "INSERT INTO m.t(c) SELECT z FROM (SELECT x AS z FROM s.u) q;"
    )
    assert rels(fx, "DERIVES_FROM") >= {
        ("sql:m.t", "sql:s.u"),
        ("sql:m.t.c", "sql:s.u.x"),
    }


def test_multi_join_reads_every_table(inline, rels) -> None:
    fx = inline(
        "SELECT a.x FROM t1 a JOIN t2 b ON a.id=b.id "
        "LEFT JOIN c.sch.t3 d ON b.k=d.k, t4 e WHERE e.m=d.m;"
    )
    assert rels(fx, "READS_FROM") == {
        ("sql:inline.sql#0", "sql:t1"),
        ("sql:inline.sql#0", "sql:t2"),
        ("sql:inline.sql#0", "sql:c.sch.t3"),
        ("sql:inline.sql#0", "sql:t4"),
    }


def test_select_star_declares_limitation_keeps_table_lineage(inline, rels, diag_codes) -> None:
    fx = inline("INSERT INTO m.t SELECT * FROM s.x;")
    assert "select-star" in diag_codes(fx)
    # linhagem de tabela permanece — a de coluna é declarada limitada
    assert rels(fx, "DERIVES_FROM") == {("sql:m.t", "sql:s.x")}
    assert not any(
        r.to_identity and "." in r.to_identity.qualified_name
        and r.from_identity and "." in r.from_identity.qualified_name
        for r in fx.relations if r.relation_type == "DERIVES_FROM"
    )


def test_ambiguous_column_never_promoted(inline, rels, diag_codes) -> None:
    fx = inline(
        "INSERT INTO m.t(c) SELECT a FROM t1 JOIN t2 ON t1.k=t2.k;"
    )
    assert "ambiguous-column" in diag_codes(fx)
    # nenhuma aresta col→col foi fabricada para `a`
    assert not rels(fx, "DERIVES_FROM") & {
        ("sql:m.t.c", "sql:t1.a"), ("sql:m.t.c", "sql:t2.a"),
    }
    # a coluna de saída existe como entidade, mas sem origem
    assert any(
        e.name == "c" and e.kind.value == "data_column"
        for e in fx.entities
    )


def test_qualified_column_resolves(inline, rels, diag_codes) -> None:
    fx = inline(
        "INSERT INTO m.t(c) SELECT t1.a FROM t1 JOIN t2 ON t1.k=t2.k;"
    )
    assert "ambiguous-column" not in diag_codes(fx)
    assert ("sql:m.t.c", "sql:t1.a") in rels(fx, "DERIVES_FROM")


def test_unqualified_single_source_resolves(inline, rels) -> None:
    fx = inline("INSERT INTO m.t(c) SELECT a FROM s.x;")
    assert ("sql:m.t.c", "sql:s.x.a") in rels(fx, "DERIVES_FROM")


def test_unresolved_qualifier_is_diagnosed(inline, diag_codes) -> None:
    fx = inline("CREATE VIEW v AS SELECT q.a FROM t1;")
    assert "unresolved-column" in diag_codes(fx)
    assert not any(
        r.relation_type == "DERIVES_FROM" and "." in (
            r.to_identity.qualified_name if r.to_identity else ""
        )
        for r in fx.relations
    )


def test_cross_schema_identity_qualified(inline) -> None:
    fx = inline(
        "SELECT a FROM cat.s1.t1 JOIN s2.t2 ON t1.id = t2.id;"
    )
    by_disp = {e.identity.display(): e.identity for e in fx.entities}
    t1 = by_disp["sql:cat.s1.t1"]
    assert t1.catalog == "cat" and t1.namespace == "s1"
    t2 = by_disp["sql:s2.t2"]
    assert t2.catalog == "" and t2.namespace == "s2"


def test_use_sets_default_schema(inline) -> None:
    fx = inline("USE analytics; SELECT a FROM t1; USE marts; SELECT b FROM t1;")
    ns = sorted(
        e.identity.namespace
        for e in fx.entities if e.kind.value == "data_table"
    )
    assert ns == ["analytics", "marts"]


def test_view_entity_kind(inline) -> None:
    fx = inline("CREATE VIEW s.v AS SELECT a FROM t;")
    ent = next(e for e in fx.entities if e.name == "v")
    assert ent.kind.value == "data_view"


def test_merge_reads_writes_and_column_edges(inline, rels) -> None:
    fx = inline(
        "MERGE INTO m.t t USING (SELECT k, v FROM s.u) s "
        "ON t.k = s.k "
        "WHEN MATCHED THEN UPDATE SET v = s.v "
        "WHEN NOT MATCHED THEN INSERT (k, v) VALUES (s.k, s.v);"
    )
    assert rels(fx, "TRANSFORMS") == {("sql:inline.sql#0", "sql:m.t")}
    assert rels(fx, "DERIVES_FROM") == {
        ("sql:m.t", "sql:s.u"),
        ("sql:m.t.v", "sql:s.u.v"),
        ("sql:m.t.k", "sql:s.u.k"),
    }


def test_update_sets_column_lineage(inline, rels) -> None:
    fx = inline("UPDATE m.t SET x = u.a FROM u WHERE t.id = u.id;")
    assert rels(fx, "TRANSFORMS") == {("sql:inline.sql#0", "sql:m.t")}
    assert ("sql:m.t.x", "sql:u.a") in rels(fx, "DERIVES_FROM")
    # UPDATE lê o próprio alvo + fonte
    assert rels(fx, "READS_FROM") == {
        ("sql:inline.sql#0", "sql:m.t"), ("sql:inline.sql#0", "sql:u"),
    }


def test_delete_transforms_target(inline, rels) -> None:
    fx = inline("DELETE FROM m.t WHERE id IN (SELECT id FROM s.u);")
    assert rels(fx, "TRANSFORMS") == {("sql:inline.sql#0", "sql:m.t")}
    assert ("sql:inline.sql#0", "sql:s.u") in rels(fx, "READS_FROM")


def test_dynamic_source_is_unresolved_text_never_fabricated(inline, diag_codes) -> None:
    fx = inline("SELECT a FROM ${tbl};")
    assert "dynamic-sql" in diag_codes(fx)
    (r,) = [r for r in fx.relations if r.relation_type == "READS_FROM"]
    assert r.to_identity is None and r.to_unresolved == "${tbl}"
    # nenhuma entidade inventada com nome dinâmico
    assert not any("${" in e.name for e in fx.entities)


def test_template_source_same_honesty(inline, diag_codes) -> None:
    fx = inline("SELECT a FROM {{ ref('model') }};")
    assert "dynamic-sql" in diag_codes(fx)
    (r,) = [r for r in fx.relations if r.relation_type == "READS_FROM"]
    assert r.to_identity is None
    assert r.to_unresolved == "{{ ref('model') }}"


def test_exec_produces_unsupported_not_relations(inline, rels, diag_codes) -> None:
    fx = inline("EXECUTE IMMEDIATE ${q}; SELECT a FROM t;")
    assert rels(fx, "READS_FROM") == {("sql:inline.sql#1", "sql:t")}
    codes = diag_codes(fx)
    assert "dynamic-sql" in codes


def test_malformed_partial_extraction_plus_diagnostics(inline, rels, diag_codes) -> None:
    fx = inline(
        "SELECT FROM ; SELECT ok FROM t1; GARBAGE; SELECT ok2 FROM t2;"
    )
    codes = diag_codes(fx)
    assert "parse-error" in codes
    # os dois selects válidos ainda extraíram — statements falhos
    # são descartados no resync e os índices reenumeram
    assert rels(fx, "READS_FROM") == {
        ("sql:inline.sql#0", "sql:t1"), ("sql:inline.sql#1", "sql:t2"),
    }


def test_unsupported_statement_diagnosed(inline, rels, diag_codes) -> None:
    fx = inline("DROP TABLE t; GRANT SELECT ON u TO r; SELECT a FROM x;")
    codes = diag_codes(fx)
    assert codes.count("unsupported-statement") == 2
    assert ("sql:inline.sql#2", "sql:x") in rels(fx, "READS_FROM")


def test_every_fact_carries_evidence(inline) -> None:
    fx = inline("CREATE TABLE m.t(a INT); INSERT INTO m.t SELECT x FROM u;")
    assert fx.evidence
    for e in fx.entities:
        assert e.evidence, e.identity.display()
        assert all(0 <= i < len(fx.evidence) for i in e.evidence)
    for r in fx.relations:
        assert r.evidence
    for ev in fx.evidence:
        assert ev.extractor == "domain:sql"
        assert ev.path == "inline.sql"


def test_unnest_is_unsupported_source_info(inline, rels, diag_codes) -> None:
    fx = inline(
        "SELECT a, x FROM t CROSS JOIN UNNEST(t.arr) AS u(x);"
    )
    assert "unsupported-source" in diag_codes(fx)
    assert ("sql:inline.sql#0", "sql:t") in rels(fx, "READS_FROM")


def test_recursive_cte_no_infinite_loop(inline) -> None:
    fx = inline(
        "WITH RECURSIVE r(n) AS ("
        "SELECT 1 UNION ALL SELECT n+1 FROM r WHERE n < 10"
        ") SELECT n FROM r;"
    )
    # não trava, não inventa tabela `r`
    assert not any(e.name == "r" for e in fx.entities)


def test_union_branches_all_read(inline, rels) -> None:
    fx = inline(
        "INSERT INTO m.t SELECT a FROM s1 UNION ALL SELECT b FROM s2;"
    )
    assert rels(fx, "READS_FROM") == {
        ("sql:inline.sql#0", "sql:s1"), ("sql:inline.sql#0", "sql:s2"),
    }
    assert rels(fx, "DERIVES_FROM") == {
        ("sql:m.t", "sql:s1"), ("sql:m.t", "sql:s2"),
    }


def test_insert_without_column_list_skips_col_lineage(inline, rels, diag_codes) -> None:
    fx = inline("INSERT INTO m.t SELECT a, b FROM s.x;")
    assert "implicit-column-list" in diag_codes(fx)
    # linhagem de tabela segue; col-col não existe
    assert rels(fx, "DERIVES_FROM") == {("sql:m.t", "sql:s.x")}


def test_column_count_mismatch_prefix_map(inline, rels, diag_codes) -> None:
    fx = inline("INSERT INTO m.t(a) SELECT x, y FROM s.x;")
    assert "column-count-mismatch" in diag_codes(fx)
    assert ("sql:m.t.a", "sql:s.x.x") in rels(fx, "DERIVES_FROM")


def test_like_table_derives(inline, rels) -> None:
    fx = inline("CREATE TABLE m.t LIKE s.x;")
    assert rels(fx, "DERIVES_FROM") == {("sql:m.t", "sql:s.x")}
    assert rels(fx, "READS_FROM") == {("sql:inline.sql#0", "sql:s.x")}


def test_correlated_subquery_reads_and_resolves(inline, rels) -> None:
    fx = inline(
        "INSERT INTO m.t(c) SELECT a FROM s.outer o "
        "WHERE EXISTS (SELECT 1 FROM s.inner i WHERE i.id = o.id);"
    )
    assert rels(fx, "READS_FROM") == {
        ("sql:inline.sql#0", "sql:s.outer"),
        ("sql:inline.sql#0", "sql:s.inner"),
    }


def test_extract_error_isolation_never_crashes(inline) -> None:
    # statement pathológico que nem o parser nem o extrator podem derrubar
    fx = inline("SELECT ((((((((((((((((a FROM t;")
    assert isinstance(fx.diagnostics, list)


def test_no_relation_without_source(inline) -> None:
    fx = inline("SELECT 1, 'x';")
    assert not fx.relations
    assert not any(
        e.kind.value == "data_transformation" for e in fx.entities
    )
