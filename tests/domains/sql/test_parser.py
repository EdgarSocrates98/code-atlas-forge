"""Parser descendente-recursivo — ANSI/Athena/Spark + resync honesto."""

from __future__ import annotations

from code_atlas.domains.sql.nodes import (
    SCreateTable,
    SCreateView,
    SDelete,
    SInsert,
    SMerge,
    SrcJoin,
    SrcSub,
    SrcTable,
    SSelect,
    SUpdate,
)
from code_atlas.domains.sql.parser import detect_dialect, parse_file


def test_select_joins_and_items() -> None:
    pf = parse_file(
        "SELECT a.x, b.y AS yy, f(a.z) g FROM t1 a JOIN t2 b "
        "ON a.id = b.id WHERE a.w > 1 ORDER BY a.x DESC LIMIT 5"
    )
    assert not pf.diagnostics
    st = pf.statements[0]
    assert isinstance(st, SSelect) and st.query is not None
    q = st.query
    assert len(q.items) == 3
    assert q.items[1].alias == "yy"
    assert q.items[2].alias == "g"
    assert len(q.sources) == 1 and isinstance(q.sources[0], SrcJoin)


def test_implicit_alias_is_not_a_colref() -> None:
    pf = parse_file("SELECT a b FROM t")
    item = pf.statements[0].query.items[0]  # type: ignore[union-attr]
    assert item.alias == "b"
    assert [c.parts for c in item.refs.cols] == [("a",)]


def test_cte_chain_and_column_list() -> None:
    pf = parse_file(
        "WITH a(k) AS (SELECT x FROM t1), "
        "b AS (SELECT k FROM a JOIN t2 ON a.k = t2.k) "
        "SELECT * FROM b"
    )
    q = pf.statements[0].query  # type: ignore[union-attr]
    assert [c.name for c in q.ctes] == ["a", "b"]
    assert q.ctes[0].columns == ("k",)


def test_set_operations_are_branches() -> None:
    pf = parse_file(
        "SELECT a FROM t1 UNION ALL SELECT b FROM t2 "
        "INTERSECT SELECT c FROM t3"
    )
    q = pf.statements[0].query  # type: ignore[union-attr]
    assert q.set_ops == ["UNION ALL", "INTERSECT"]
    assert len(q.set_branches) == 2


def test_insert_overwrite_and_partition() -> None:
    pf = parse_file(
        "INSERT OVERWRITE TABLE m.t PARTITION(ds='2024-01-01') "
        "SELECT a, b FROM s.x"
    )
    st = pf.statements[0]
    assert isinstance(st, SInsert)
    assert st.overwrite and st.target and st.target.parts == ("m", "t")
    assert st.query is not None


def test_insert_column_list() -> None:
    pf = parse_file("INSERT INTO t (a, b) SELECT x, y FROM u")
    st = pf.statements[0]
    assert isinstance(st, SInsert) and st.columns == ("a", "b")


def test_insert_values() -> None:
    pf = parse_file("INSERT INTO t (a, b) VALUES (1, 'x'), (2, 'y')")
    st = pf.statements[0]
    assert isinstance(st, SInsert)
    assert len(st.value_rows) == 2 and st.query is None


def test_update_with_from() -> None:
    pf = parse_file(
        "UPDATE m.t SET x = u.a, y = u.b FROM u WHERE t.id = u.id"
    )
    st = pf.statements[0]
    assert isinstance(st, SUpdate)
    assert st.target and st.target.parts == ("m", "t")
    assert [c for c, _ in st.sets] == ["x", "y"]
    assert len(st.sources) == 1 and isinstance(st.sources[0], SrcTable)


def test_delete_with_using() -> None:
    pf = parse_file("DELETE FROM t USING u WHERE t.id = u.id")
    st = pf.statements[0]
    assert isinstance(st, SDelete) and st.target
    assert len(st.sources) == 1


def test_merge_full_shape() -> None:
    pf = parse_file(
        "MERGE INTO m.t t USING (SELECT k, v FROM s.u) s "
        "ON t.k = s.k "
        "WHEN MATCHED THEN UPDATE SET v = s.v "
        "WHEN NOT MATCHED THEN INSERT (k, v) VALUES (s.k, s.v)"
    )
    st = pf.statements[0]
    assert isinstance(st, SMerge)
    assert st.target_alias == "t"
    assert isinstance(st.source, SrcSub) and st.source.alias == "s"
    assert st.sets and st.sets[0][0] == "v"
    assert st.insert_cols == ("k", "v")
    assert len(st.insert_exprs) == 2


def test_ctas_and_create_variants() -> None:
    pf = parse_file(
        "CREATE TABLE a.b (x INT, y STRING, PRIMARY KEY (x));"
        "CREATE OR REPLACE VIEW v AS SELECT x FROM a.b;"
        "CREATE TEMP TABLE c LIKE a.b;"
        "CREATE TABLE d STORED AS PARQUET AS SELECT x FROM a.b;"
    )
    kinds = [type(s) for s in pf.statements]
    assert kinds == [SCreateTable, SCreateView, SCreateTable, SCreateTable]
    ddl, view, like, ctas = pf.statements
    assert isinstance(ddl, SCreateTable)
    assert [c for c, _ in ddl.columns] == ["x", "y"]
    assert isinstance(view, SCreateView) and view.or_replace
    assert isinstance(like, SCreateTable) and like.like
    assert isinstance(ctas, SCreateTable) and ctas.query is not None


def test_use_and_truncate() -> None:
    pf = parse_file("USE cat.sch; TRUNCATE TABLE m.t;")
    assert pf.statements[0].kind == "use"
    assert pf.statements[1].kind == "truncate"


def test_star_and_qualified_star() -> None:
    pf = parse_file("SELECT *, t.*, count(*) FROM t")
    items = pf.statements[0].query.items  # type: ignore[union-attr]
    assert items[0].star is not None and items[0].star.qualifier == ()
    assert items[1].star is not None and items[1].star.qualifier == ("t",)
    # count(*) é expressão — star interno, não item-estrela
    assert items[2].star is None
    assert items[2].refs.stars


def test_dynamic_marks_statement() -> None:
    pf = parse_file("SELECT a FROM ${tbl}; SELECT b FROM t;")
    assert pf.statements[0].dynamic
    assert not pf.statements[1].dynamic


def test_exec_is_unsupported_dynamic() -> None:
    pf = parse_file("EXECUTE IMMEDIATE ${q}; SELECT a FROM t;")
    assert pf.statements[0].kind == "unsupported"
    assert pf.statements[1].kind == "select"


def test_malformed_resync_keeps_partial() -> None:
    pf = parse_file(
        "SELECT FROM ; SELECT ok1 FROM t1; GARBAGE HERE; "
        "SELECT ok2 FROM t2"
    )
    kinds = [s.kind for s in pf.statements]
    assert kinds == ["select", "select"]
    codes = [d.code for d in pf.diagnostics]
    assert codes.count("parse-error") == 2


def test_unclosed_parens_dont_crash() -> None:
    pf = parse_file("SELECT (a + (b FROM (t;")
    assert pf.diagnostics  # parse-error registrado, nunca exceção


def test_dialect_detection() -> None:
    assert detect_dialect("INSERT OVERWRITE TABLE t SELECT 1") == "spark"
    assert detect_dialect("SELECT * FROM t CROSS JOIN UNNEST(a) u") == "athena"
    assert detect_dialect("SELECT a -> b FROM t") == "athena"
    assert detect_dialect("SELECT a FROM t") == "ansi"


def test_athena_unnest_is_unsupported_source_not_crash() -> None:
    pf = parse_file("SELECT a FROM t CROSS JOIN UNNEST(t.arr) AS u(x)")
    q = pf.statements[0].query  # type: ignore[union-attr]
    assert q.sources  # UNNEST vira SrcUnsupported, o resto parseia


def test_spark_lateral_view_doesnt_crash() -> None:
    pf = parse_file(
        "SELECT a FROM t LATERAL VIEW explode(t.arr) lv AS x"
    )
    assert pf.statements[0].kind == "select"


def test_prepared_select_is_select() -> None:
    pf = parse_file("PREPARE q FROM SELECT a FROM t")
    st = pf.statements[0]
    assert isinstance(st, SSelect) and st.prepared_name == "q"


def test_lambda_params_not_colrefs() -> None:
    pf = parse_file(
        "SELECT filter(xs, x -> x > 0) FROM t"
    )
    item = pf.statements[0].query.items[0]  # type: ignore[union-attr]
    assert [c.parts for c in item.refs.cols] == [("xs",)]


def test_deep_paren_nesting_is_diagnostic_not_recursion_error() -> None:
    # _MAX_DEPTH (32) propaga join_chain → primary → _primary_paren —
    # parênteses patológicos viram parse-error no statement, não crash.
    deep = "SELECT a FROM " + "(" * 80 + "t" + ")" * 80
    pf = parse_file(deep)
    assert pf.diagnostics  # parse-error registrado; parser sobrevive
