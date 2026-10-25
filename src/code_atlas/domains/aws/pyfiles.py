"""Extração estática de scripts ``.py`` AWS-data (lf-0113, Phase 5).

Análise por ``ast`` (stdlib) — nenhuma execução do script, nenhum SDK:

- **Glue**: ``GlueContext``/``awsglue`` marcam o script;
  ``create_dynamic_frame.from_catalog``/``getDataFrame`` →
  ``READS_FROM`` de ``data_table`` de catálogo; ``from_options``/
  ``getSource`` → ``READS_FROM`` de path ``s3://`` (ou referência
  externa a conexão nomeada); ``write_dynamic_frame.*``/
  ``write_from_options``/``getSink`` → ``WRITES_TO``;
  ``job.init``/``job.commit``/``getResolvedOptions`` viram atributos
  (bookmarks/args observados — nomes de opções, nunca valores
  resolvidos em runtime);
- **Spark/Iceberg**: ``spark.sql(<literal>)``/``session.sql``/``execute``
  com literal SQL → linhagem via ``sqlbridge`` (transformações
  ``path@L<linha>#<stmt>``); ``df.writeTo("c.n.t")`` seguido de
  ``.using("iceberg")`` ou catálogo ``spark.sql.catalog.<c>`` com impl
  iceberg → ``iceberg_table``; ``insertInto``/``saveAsTable``/
  ``spark.table``/``spark.read.<fmt>(s3|nome)`` → arestas diretas;
  ``MERGE``/``INSERT``/``UPDATE``/``DELETE`` dentro de literais vêm do
  parser sql;
- **Athena SDK**: ``start_query_execution(QueryString=<literal>)`` →
  mesmo pipeline SQL com ``USE`` sintético só quando
  ``QueryExecutionContext`` declara Database/Catalog; ``OutputLocation``
  → ``WRITES_TO`` s3;
- **IAM**: ``create_job``/literal ``arn:aws:iam::…:role/x`` →
  ``iam_role`` entity + ``ASSUMES_ROLE`` (external — o role vive na
  conta, fora do snapshot); ARN solto sem contexto de papel →
  ``REFERENCES`` external;
- **S3**: literal ``s3://`` fora de call reconhecida → ``REFERENCES``
  (direção desconhecida — nunca inventar leitura/escrita);
- identidade conta/região: ``catalog_id`` literal vira
  ``aws:glue:<acct>``; segmentos ausentes ficam vazios — identidades
  sem conta NÃO colidem com as que a têm.

Scripts sem marcadores AWS-data → diagnóstico ``not-aws-data-artifact``
e zero fatos. ``SyntaxError`` → ``py-parse-error`` + varredura
degradada por ``tokenize`` apenas de literais ``s3://``/ARN (modo
degradado declarado, nunca estrutura inventada).
"""

from __future__ import annotations

import ast
import io
import posixpath
import re
import tokenize
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from code_atlas.contracts.enums import ResolutionStatus
from code_atlas.domains.aws.common import (
    ARN_RE,
    ICEBERG_META,
    aws_catalog,
    is_iam_role_arn,
    is_iceberg_impl,
    line_starts,
    node_span,
    parse_arn,
    parse_s3_uri,
    redact_if_sensitive,
    split_table_name,
)
from code_atlas.domains.aws.emit import Sink
from code_atlas.domains.aws.sqlbridge import (
    extract_sql_fragment,
    literal_span_remap,
    s3_entity,
)
from code_atlas.domains.identity import DomainIdentity
from code_atlas.domains.model import DomainEntityKind, DomainRelationKind
from code_atlas.domains.sql.adapter import looks_like_sql
from code_atlas.parsers.contracts import SourceSpan

_READS = str(DomainRelationKind.READS_FROM)
_WRITES = str(DomainRelationKind.WRITES_TO)
_REFS = str(DomainRelationKind.REFERENCES)
_ASSUMES = str(DomainRelationKind.ASSUMES_ROLE)

# Cadeias de atributos que denotam leitura/escrita no catálogo Glue.
_READ_CATALOG_SUFFIXES = (
    "create_dynamic_frame.from_catalog",
    "create_dynamic_frame_from_catalog",
    "getDataFrame",
    "get_data_frame",
)
_READ_OPTIONS_SUFFIXES = (
    "create_dynamic_frame.from_options",
    "create_dynamic_frame_from_options",
    "getSource",
    "get_source",
)
_WRITE_CATALOG_SUFFIXES = (
    "write_dynamic_frame.from_catalog",
    "write_dynamic_frame_from_catalog",
)
_WRITE_OPTIONS_SUFFIXES = (
    "write_dynamic_frame.from_options",
    "write_dynamic_frame_from_options",
    "write_from_options",
    "write.from_options",
    "getSink",
    "get_sink",
)

# receivers aceitos para ``X.sql(...)``/``X.table(...)`` etc. — reduz
# falsos positivos de ``session.sql`` fora do contexto Spark.
_SPARK_ROOTS = re.compile(
    r"^(spark|sparkSession|spark_session|sqlContext|sql_context"
    r"|sparkSessionBuilder|glueContext|glue_context|glue_context"
    r"|sparkContext|sc|sess|session|emr|sparky)$",
    re.IGNORECASE,
)

_ICEBERG_FORMATS = frozenset({"iceberg"})
_SPARK_READ_FMTS = frozenset({"load", "parquet", "json", "csv", "orc", "avro", "text"})
_WRITE_TERMINALS = frozenset(
    {
        "append",
        "overwritePartitions",
        "overwrite",
        "replace",
        "create",
        "createOrReplace",
        "insertInto",
    }
)


@dataclass
class _PyFacts:
    """Observações de primeira passada de um script ``.py``."""

    tree: ast.AST | None = None
    parents: dict[int, ast.AST] = field(default_factory=dict)
    calls: list[ast.Call] = field(default_factory=list)
    assigns: dict[str, str] = field(default_factory=dict)
    str_nodes: list[ast.Constant] = field(default_factory=list)
    markers: set[str] = field(default_factory=set)
    spark_vars: set[str] = field(default_factory=set)
    declared_catalogs: dict[str, str] = field(default_factory=dict)
    job_inits: list[ast.Call] = field(default_factory=list)
    job_commits: list[ast.Call] = field(default_factory=list)
    resolved_options: list[tuple[str, ...]] = field(default_factory=list)


def _dotted(node: ast.AST) -> str:
    """Caminho pontilhado do callee/receiver (``a.b.c`` → ``"a.b.c"``).

    Desce por ``Call.func`` — ``spark.read.format("x").load`` forma a
    cadeia ``spark.read.format.load``.
    """
    parts: list[str] = []
    cur: ast.AST = node
    seen = 0
    while seen < 32:
        seen += 1
        if isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
            continue
        if isinstance(cur, ast.Call):
            cur = cur.func
            continue
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        break
    parts.reverse()
    return ".".join(parts)


def _call_root(call: ast.Call) -> str:
    """Nome raiz do receiver — ``spark.read.format("x").load`` → spark."""
    func = call.func
    if isinstance(func, ast.Attribute):
        return _dotted(func.value).split(".")[0]
    return ""


def _str_lit(node: ast.AST | None) -> str | None:
    """Valor de ``ast.Constant`` str; ``None`` para JoinedStr/dinâmico."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _kw(call: ast.Call, name: str) -> ast.AST | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _kw_lit(call: ast.Call, name: str) -> str | None:
    return _str_lit(_kw(call, name))


def _pos_lit(call: ast.Call, i: int) -> str | None:
    if i < len(call.args):
        return _str_lit(call.args[i])
    return None


def _pos_or_kw(call: ast.Call, i: int, name: str) -> str | None:
    """kwarg ``name`` ou posicional ``i`` — assinaturas Glue/Spark."""
    v = _kw_lit(call, name)
    return v if v is not None else _pos_lit(call, i)


def _literal(node: ast.AST | None) -> object | None:
    """``ast.literal_eval`` seguro — ``None`` para não-literais."""
    if node is None:
        return None
    try:
        result: object = ast.literal_eval(node)
        return result
    except (ValueError, TypeError, SyntaxError, MemoryError):
        return None


def _dict_lit(node: ast.AST | None) -> dict[str, object] | None:
    val = _literal(node)
    if isinstance(val, dict):
        return {str(k): v for k, v in val.items()}
    return None


class _Scanner(ast.NodeVisitor):
    """Primeira passada: coleta calls/strings/marcadores."""

    def __init__(self) -> None:
        self.fx = _PyFacts()

    def visit(self, node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            self.fx.parents[id(child)] = node
        super().visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            if a.name.split(".")[0] in ("awsglue", "pyspark", "boto3"):
                self.fx.markers.add(
                    {"awsglue": "glue", "pyspark": "spark", "boto3": "aws-sdk"}[
                        a.name.split(".")[0]
                    ]
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        if mod.startswith("awsglue"):
            self.fx.markers.add("glue")
        if mod.startswith("pyspark"):
            self.fx.markers.add("spark")
        if mod.startswith("boto3"):
            self.fx.markers.add("aws-sdk")
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if isinstance(node.value, ast.Call):
            dotted = _dotted(node.value.func)
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    self.fx.assigns[tgt.id] = dotted
                    if dotted.endswith("GlueContext") or ("awsglue" in dotted):
                        self.fx.markers.add("glue")
                        self.fx.spark_vars.add(tgt.id)
                    if "SparkSession" in dotted:
                        self.fx.markers.add("spark")
                        self.fx.spark_vars.add(tgt.id)
        elif isinstance(node.value, ast.Attribute):
            dotted = _dotted(node.value)
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    self.fx.assigns[tgt.id] = dotted
                    if dotted.endswith("spark_session"):
                        self.fx.spark_vars.add(tgt.id)
                        self.fx.markers.add("spark")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        self.fx.calls.append(node)
        dotted = _dotted(node.func)
        last = dotted.rsplit(".", 1)[-1]
        if last in ("GlueContext", "Job"):
            self.fx.markers.add("glue")
        if last in ("SparkSession", "getOrCreate"):
            self.fx.markers.add("spark")
        if last == "getResolvedOptions":
            self.fx.markers.add("glue")
            self._options(node)
        if last in ("sql", "writeTo") or (
            last in ("read", "table", "write")
            and (
                _call_root(node) in self.fx.spark_vars or bool(_SPARK_ROOTS.match(_call_root(node)))
            )
        ):
            self.fx.markers.add("spark")
        if last in (
            "start_query_execution",
            "create_job",
            "update_job",
            "add_steps",
            "run_job_flow",
        ):
            self.fx.markers.add("aws-sdk")
        if last == "init" and self._is_job_call(node):
            self.fx.job_inits.append(node)
            self.fx.markers.add("glue")
        if last in ("commit", "run") and self._is_job_call(node):
            self.fx.job_commits.append(node)
            self.fx.markers.add("glue")
        self.generic_visit(node)

    def _is_job_call(self, node: ast.Call) -> bool:
        """``job.init/commit/run`` — receiver ``job``/``Job``-bound."""
        if not isinstance(node.func, ast.Attribute):
            return False
        root = _dotted(node.func.value).split(".")[0]
        return (
            root in self.fx.spark_vars
            or self.fx.assigns.get(root, "").endswith("Job")
            or root == "job"
        )

    def _options(self, node: ast.Call) -> None:
        """``getResolvedOptions(sys.argv, [...])`` → nomes observados."""
        if len(node.args) >= 2:
            lst = _literal(node.args[1])
            if isinstance(lst, (list, tuple)):
                self.fx.resolved_options.append(tuple(str(v) for v in lst))

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            self.fx.str_nodes.append(node)
        self.generic_visit(node)


def extract_py(
    sink: Sink,
    *,
    path: str,
    text: str,
    sha: str,
) -> None:
    """Extrai um script ``.py`` — fatos AWS-data evidence-first."""
    starts = line_starts(text)
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError) as exc:
        sink.diag(
            "py-parse-error",
            "error",
            f"Python does not parse: {exc}",
            path,
        )
        _degraded_scan(sink, path=path, text=text, sha=sha, starts=starts)
        return
    sc = _Scanner()
    sc.visit(tree)
    facts = sc.fx
    facts.tree = tree

    if not facts.markers and not _has_aws_observations(facts):
        sink.diag(
            "not-aws-data-artifact",
            "info",
            "no Glue/Spark/Athena markers — skipped",
            path,
        )
        return

    # catálogos declarados precisam preceder as arestas que os usam
    _collect_catalogs(sink, facts, path, sha, starts)
    job = _script_entity(sink, facts, path=path, sha=sha, starts=starts)
    used_strings: set[int] = set()

    for call in facts.calls:
        _handle_call(
            sink,
            facts,
            call,
            job,
            path=path,
            text=text,
            sha=sha,
            starts=starts,
            used=used_strings,
        )

    _job_call_args(
        sink,
        facts,
        job,
        path=path,
        sha=sha,
        starts=starts,
        used=used_strings,
    )
    _sweep_literals(
        sink,
        facts,
        job,
        path=path,
        sha=sha,
        starts=starts,
        used=used_strings,
    )
    sink.bump("py_files")


def _has_aws_observations(facts: _PyFacts) -> bool:
    """Sem marcadores: ainda observável se há literal s3/arn ou call SDK."""
    for node in facts.str_nodes:
        v = node.value
        if isinstance(v, str) and (parse_s3_uri(v) is not None or v.startswith("arn:aws:")):
            return True
    return any(
        _dotted(c.func).rsplit(".", 1)[-1] in ("start_query_execution", "create_job")
        for c in facts.calls
    )


def _script_entity(
    sink: Sink,
    facts: _PyFacts,
    *,
    path: str,
    sha: str,
    starts: tuple[int, ...],
) -> DomainIdentity:
    """Entidade-âncora do script — kind honesto pelo marcador dominante."""
    namespace = posixpath.dirname(path)
    stem = PurePosixPath(path).stem or PurePosixPath(path).name
    if "glue" in facts.markers:
        kind = DomainEntityKind.GLUE_JOB
    elif "spark" in facts.markers:
        kind = DomainEntityKind.SPARK_JOB
    else:
        kind = DomainEntityKind.DATA_PIPELINE
    attrs: dict[str, str] = {
        "artifact_kind": "py-script",
        "script": PurePosixPath(path).name,
    }
    if facts.job_inits:
        attrs["job_init"] = "true"
    if facts.job_commits:
        attrs["job_commit_or_run"] = "true"
    if facts.resolved_options:
        names: list[str] = []
        for tup in facts.resolved_options:
            names.extend(tup)
        attrs["job_option_names"] = ",".join(sorted(set(names)))
        if "JOB_BOOKMARK_OPTION" in {n.lstrip("-") for n in names}:
            attrs["job_bookmark_option_listed"] = "true"
    return sink.entity(
        kind=kind,
        name=stem,
        qualified_name=stem,
        namespace=namespace,
        path=path,
        span=None,
        content_hash=sha,
        attributes=attrs,
        evidence=(sink.evi(path, sha, None, notes="script"),),
    )


# -- catálogos declarados ---------------------------------------------------


def _register_catalog(
    sink: Sink,
    name: str,
    impl: str,
    *,
    path: str,
    sha: str,
    span: SourceSpan | None,
) -> None:
    """``spark.sql.catalog.<name>`` → ``data_catalog`` + registro."""
    attrs = {"declared_by": path}
    if impl:
        attrs["catalog_impl"] = impl
        if is_iceberg_impl(impl):
            attrs["engine"] = "iceberg"
    ident = sink.entity(
        kind=DomainEntityKind.DATA_CATALOG,
        name=name,
        qualified_name=name,
        path=path,
        span=span,
        content_hash=sha,
        attributes=attrs,
        evidence=(sink.evi(path, sha, span, notes="catalog-decl"),),
    )
    sink.register_catalog(name, ident)
    sink.bump("catalogs")


def _collect_catalogs(
    sink: Sink,
    facts: _PyFacts,
    path: str,
    sha: str,
    starts: tuple[int, ...],
) -> None:
    """``conf.set("spark.sql.catalog.<n>", "<impl>")`` → catálogos."""
    for call in facts.calls:
        dotted = _dotted(call.func)
        if not dotted.endswith("conf.set") and not dotted.endswith("setConf"):
            continue
        key = _pos_lit(call, 0) or _kw_lit(call, "key")
        val = _pos_lit(call, 1) or _kw_lit(call, "value")
        if not key or not key.startswith("spark.sql.catalog."):
            continue
        rest = key[len("spark.sql.catalog.") :]
        cat, _, prop = rest.partition(".")
        span = node_span(starts, call)
        if not prop:
            _register_catalog(
                sink,
                cat,
                val or "",
                path=path,
                sha=sha,
                span=span,
            )
            facts.declared_catalogs[cat] = val or ""
            if val and is_iceberg_impl(val):
                facts.markers.add("iceberg")
            continue
        # propriedade de catálogo já declarado (ex.: ``.warehouse``)
        cat_ident = sink.catalogs.get(cat)
        if cat_ident is not None and val and prop == "warehouse" and parse_s3_uri(val) is not None:
            evi = sink.evi(path, sha, span, notes="catalog-warehouse")
            s3 = s3_entity(sink, val, path=path, sha=sha, span=span, evi=evi)
            if s3 is not None:
                sink.rel(
                    _REFS,
                    path,
                    cat_ident,
                    to_identity=s3,
                    span=span,
                    evidence=(evi,),
                )


def _is_spark_receiver(facts: _PyFacts, call: ast.Call) -> bool:
    root = _call_root(call)
    return (
        root in facts.spark_vars
        or bool(_SPARK_ROOTS.match(root))
        or facts.assigns.get(root, "").endswith(("GlueContext",))
    )


def _table_kind(catalog: str, iceberg: bool) -> DomainEntityKind:
    return DomainEntityKind.ICEBERG_TABLE if iceberg else DomainEntityKind.DATA_TABLE


def _table_entity(
    sink: Sink,
    written: str,
    *,
    iceberg: bool,
    path: str,
    sha: str,
    span: SourceSpan | None,
    evi: int,
) -> DomainIdentity | None:
    """``cat.db.t`` → ``data_table``/``iceberg_table``; meta-suffix ok."""
    cat, ns, name = split_table_name(written)
    if not name:
        return None
    attrs = {"written_name": written}
    if name.lower() in ICEBERG_META and ns:
        attrs["iceberg_meta_table"] = name.lower()
        iceberg = True
    return sink.entity(
        kind=_table_kind(cat, iceberg),
        name=name,
        qualified_name=name,
        catalog=cat,
        namespace=ns,
        path=path,
        span=span,
        content_hash=sha,
        attributes=attrs,
        evidence=(evi,),
    )


def _catalog_ref_for(
    sink: Sink,
    written: str,
    subject: DomainIdentity,
    path: str,
    span: SourceSpan | None,
    evi: int,
) -> None:
    """Catálogo nomeado de um ``cat.db.t`` → ``REFERENCES`` adiada."""
    cat = split_table_name(written)[0]
    if cat:
        sink.catalog_ref(subject, cat, path, span, evi)


# -- handlers por call ------------------------------------------------------


def _catalog_read(
    sink: Sink,
    call: ast.Call,
    subject: DomainIdentity,
    *,
    path: str,
    sha: str,
    starts: tuple[int, ...],
) -> None:
    """``from_catalog``/``getDataFrame`` — leitura de tabela Glue."""
    database = _pos_or_kw(call, 0, "database")
    table = _pos_or_kw(call, 1, "table_name") or _kw_lit(call, "table")
    catalog_id = _kw_lit(call, "catalog_id") or _kw_lit(call, "CatalogId")
    span = node_span(starts, call)
    evi = sink.evi(path, sha, span, notes="glue-catalog-read")
    catalog = aws_catalog("glue", catalog_id or "")
    if catalog_id:
        _declare_glue_catalog(sink, catalog, subject, path=path, sha=sha, span=span)
    if not database or not table:
        sink.diag(
            "dynamic-catalog-ref",
            "warning",
            "catalog read with non-literal database/table — relation kept unresolved",
            path,
        )
        sink.rel(
            _READS,
            path,
            subject,
            to_unresolved="<dynamic>",
            status=ResolutionStatus.UNRESOLVED,
            span=span,
            evidence=(evi,),
        )
        return
    ident = sink.entity(
        kind=DomainEntityKind.DATA_TABLE,
        name=table,
        qualified_name=table,
        catalog=catalog,
        namespace=database,
        path=path,
        span=span,
        content_hash=sha,
        attributes={
            "written_name": f"{database}.{table}",
            "source": "glue-catalog",
        },
        evidence=(evi,),
    )
    sink.rel(
        _READS,
        path,
        subject,
        to_identity=ident,
        span=span,
        evidence=(evi,),
    )
    sink.bump("glue_reads")


def _declare_glue_catalog(
    sink: Sink,
    catalog: str,
    subject: DomainIdentity,
    *,
    path: str,
    sha: str,
    span: SourceSpan | None,
) -> None:
    """``catalog_id`` explícito → ``data_catalog`` + REFERENCES."""
    ident = sink.entity(
        kind=DomainEntityKind.DATA_CATALOG,
        name=catalog,
        qualified_name=catalog,
        path=path,
        span=span,
        content_hash=sha,
        attributes={"source": "glue-catalog-id"},
        evidence=(sink.evi(path, sha, span, notes="glue-catalog-id"),),
    )
    sink.register_catalog(catalog, ident)
    sink.rel(
        _REFS,
        path,
        subject,
        to_identity=ident,
        span=span,
        evidence=(sink.evi(path, sha, span, notes="catalog-ref"),),
    )


def _catalog_write(
    sink: Sink,
    call: ast.Call,
    subject: DomainIdentity,
    *,
    path: str,
    sha: str,
    starts: tuple[int, ...],
) -> None:
    """``write_dynamic_frame.from_catalog`` — escrita em tabela Glue."""
    database = _kw_lit(call, "database")
    table = _kw_lit(call, "table_name") or _kw_lit(call, "table")
    if database is None:
        database = _pos_lit(call, 1) or _pos_lit(call, 0)
    if table is None:
        table = _pos_lit(call, 2)
    catalog_id = _kw_lit(call, "catalog_id") or _kw_lit(call, "CatalogId")
    span = node_span(starts, call)
    evi = sink.evi(path, sha, span, notes="glue-catalog-write")
    catalog = aws_catalog("glue", catalog_id or "")
    if catalog_id:
        _declare_glue_catalog(sink, catalog, subject, path=path, sha=sha, span=span)
    if not database or not table:
        sink.diag(
            "dynamic-catalog-ref",
            "warning",
            "catalog write with non-literal database/table",
            path,
        )
        sink.rel(
            _WRITES,
            path,
            subject,
            to_unresolved="<dynamic>",
            status=ResolutionStatus.UNRESOLVED,
            span=span,
            evidence=(evi,),
        )
        return
    ident = sink.entity(
        kind=DomainEntityKind.DATA_TABLE,
        name=table,
        qualified_name=table,
        catalog=catalog,
        namespace=database,
        path=path,
        span=span,
        content_hash=sha,
        attributes={
            "written_name": f"{database}.{table}",
            "source": "glue-catalog",
        },
        evidence=(evi,),
    )
    sink.rel(
        _WRITES,
        path,
        subject,
        to_identity=ident,
        span=span,
        evidence=(evi,),
    )
    sink.bump("glue_writes")


def _conn_type_of(call: ast.Call) -> str | None:
    """``connection_type=`` kw ou posicional ``write_from_options``."""
    conn_type = _kw_lit(call, "connection_type")
    if conn_type is not None:
        return conn_type
    for i in range(len(call.args)):
        v = _pos_lit(call, i)
        if isinstance(v, str) and v.lower() in _CONN_TYPES:
            return v
    return None


def _conn_opts_dict(call: ast.Call) -> dict[str, object] | None:
    """``connection_options=`` literal dict — kw ou 1º dict posicional."""
    opts = _dict_lit(_kw(call, "connection_options"))
    if opts is None:
        opts = _dict_lit(_kw(call, "connectionOptions"))
    if opts is None:
        for a in call.args:
            d = _dict_lit(a)
            if d is not None:
                opts = d
                break
    return opts


def _connection_fallback(
    sink: Sink,
    call: ast.Call,
    subject: DomainIdentity,
    *,
    direction: str,
    conn_type: str | None,
    opts: dict[str, object] | None,
    path: str,
    span: SourceSpan | None,
    evi: int,
) -> None:
    """Alvos não-literais → diagnóstico/aresta honesta."""
    if conn_type is None or opts is None:
        sink.diag(
            "dynamic-connection",
            "warning",
            "connection options not a literal dict — targets unknown",
            path,
        )
        return
    if conn_type:
        sink.rel(
            _REFS,
            path,
            subject,
            to_unresolved=f"glue-connection:{conn_type}",
            status=ResolutionStatus.EXTERNAL,
            span=span,
            evidence=(evi,),
        )
        return
    sink.rel(
        direction,
        path,
        subject,
        to_unresolved="<dynamic>",
        status=ResolutionStatus.UNRESOLVED,
        span=span,
        evidence=(evi,),
    )


def _connection_options(
    sink: Sink,
    call: ast.Call,
    subject: DomainIdentity,
    *,
    direction: str,
    path: str,
    sha: str,
    starts: tuple[int, ...],
) -> None:
    """``from_options``/``write_from_options`` — s3/jdbc/connection."""
    span = node_span(starts, call)
    evi = sink.evi(path, sha, span, notes="connection-options")
    conn_type = _conn_type_of(call)
    opts = _conn_opts_dict(call)
    conn_name = _kw_lit(call, "connection_name") or _kw_lit(call, "connectionName")
    handled = False
    if opts:
        handled = _emit_option_targets(
            sink,
            conn_type,
            opts,
            subject,
            direction=direction,
            path=path,
            sha=sha,
            span=span,
            evi=evi,
        )
    if conn_name:
        sink.rel(
            _REFS,
            path,
            subject,
            to_unresolved=f"glue-connection:{conn_name}",
            status=ResolutionStatus.EXTERNAL,
            span=span,
            evidence=(evi,),
        )
        handled = True
    if not handled:
        _connection_fallback(
            sink,
            call,
            subject,
            direction=direction,
            conn_type=conn_type,
            opts=opts,
            path=path,
            span=span,
            evi=evi,
        )


_CONN_TYPES = frozenset(
    {
        "s3",
        "dynamodb",
        "jdbc",
        "mysql",
        "postgresql",
        "oracle",
        "sqlserver",
        "redshift",
        "mongodb",
        "documentdb",
        "kinesis",
        "kafka",
        "marketplace",
        "custom.jdbc",
        "snowflake",
        "bigquery",
        "opensearch",
        "saphana",
        "teradata",
        "vertica",
        "netezza",
        "azure",
        "cosmos",
        "db2",
        "informix",
        "sybase",
        "excel",
        "athena",
        "salesforce",
        "servicenow",
    }
)


def _emit_option_targets(
    sink: Sink,
    conn_type: str | None,
    opts: dict[str, object],
    subject: DomainIdentity,
    *,
    direction: str,
    path: str,
    sha: str,
    span: SourceSpan | None,
    evi: int,
) -> bool:
    """Emite arestas ``direction`` para alvos de ``connection_options``."""
    handled = False
    raw_paths = opts.get("paths", opts.get("path"))
    paths: list[str] = []
    if isinstance(raw_paths, str):
        paths = [raw_paths]
    elif isinstance(raw_paths, (list, tuple)):
        paths = [str(p) for p in raw_paths]
    for p in paths:
        ident = s3_entity(sink, p, path=path, sha=sha, span=span, evi=evi)
        if ident is None:
            sink.diag(
                "dynamic-s3-uri",
                "warning",
                "s3 path in connection_options is not a literal URI",
                path,
            )
            continue
        sink.rel(
            direction,
            path,
            subject,
            to_identity=ident,
            span=span,
            evidence=(evi,),
        )
        sink.bump("s3_paths")
        handled = True
    if conn_type == "dynamodb":
        for key in ("dynamodb.input.tableName", "dynamodb.output.tableName"):
            val = opts.get(key)
            if isinstance(val, str) and val:
                sink.rel(
                    _REFS,
                    path,
                    subject,
                    to_unresolved=f"dynamodb:{val}",
                    status=ResolutionStatus.EXTERNAL,
                    span=span,
                    evidence=(evi,),
                )
                handled = True
    return handled


def _sql_literal_call(
    sink: Sink,
    call: ast.Call,
    subject: DomainIdentity,
    *,
    arg_node: ast.AST | None,
    path: str,
    text: str,
    sha: str,
    starts: tuple[int, ...],
    use_catalog: str = "",
    use_schema: str = "",
    notes: str = "sql-literal",
) -> None:
    """Literal SQL → ``sqlbridge``; dinâmico → diagnóstico honesto."""
    span = node_span(starts, call)
    evi = sink.evi(path, sha, span, notes=notes)
    if arg_node is None:
        return
    if isinstance(arg_node, ast.JoinedStr) or (not isinstance(arg_node, ast.Constant)):
        sink.diag(
            "dynamic-sql-literal",
            "warning",
            "SQL argument is not a string literal — never parsed",
            path,
        )
        sink.rel(
            "REFERENCES",
            path,
            subject,
            to_unresolved="<dynamic-sql>",
            status=ResolutionStatus.UNRESOLVED,
            span=span,
            evidence=(evi,),
        )
        return
    sql = arg_node.value
    if not isinstance(sql, str) or not sql.strip():
        return
    # remap: corpo do literal contíguo no fonte → offsets reais
    body_start, contiguous = _literal_body_start(text, arg_node, sql)
    remap = literal_span_remap(
        file_text=text,
        line_starts=starts,
        body_char_start=body_start if body_start is not None else 0,
        contiguous=contiguous and body_start is not None,
        fallback=node_span(starts, arg_node) or span,
    )
    frag = extract_sql_fragment(
        sink,
        sql_text=sql,
        path=path,
        sha=sha,
        transform_tag=f"L{arg_node.lineno}",
        span_remap=remap,
        use_catalog=use_catalog,
        use_schema=use_schema,
        fallback_anchor=subject,
    )
    # a script contém literalmente os statements — CONTAINS honesto
    for tident in frag.transform_idents:
        sink.rel(
            "CONTAINS",
            path,
            subject,
            to_identity=tident,
            span=span,
            evidence=(evi,),
        )


def _literal_body_start(text: str, node: ast.AST, decoded: str) -> tuple[int | None, bool]:
    """Início do corpo decodificado do literal no fonte + contiguidade.

    Retorna ``(char_start, contiguous)``; ``contiguous=False`` quando
    escapes/concatenação impedem mapear offsets internos — o caller cai
    para o span do literal inteiro (nunca offset errado).
    """
    lineno = getattr(node, "lineno", None)
    col = getattr(node, "col_offset", None)
    if lineno is None or col is None:
        return None, False
    lines = text.splitlines(keepends=True)
    if lineno < 1 or lineno > len(lines):
        return None, False
    line_start_char = sum(len(ln) for ln in lines[: lineno - 1])
    # col_offset é byte offset na linha → converter para char
    line = lines[lineno - 1]
    char_col = len(line.encode("utf-8")[:col].decode("utf-8", "replace"))
    start = line_start_char + char_col
    raw = ast.get_source_segment(text, node)
    if raw is None:
        return None, False
    m = re.match(r"(?i)([rubf]*)('''|\"\"\"|'|\")", raw)
    if m is None:
        return None, False
    body_start = start + m.end()
    contiguous = text[body_start : body_start + len(decoded)] == decoded
    return body_start, contiguous


def _role_entity(
    sink: Sink,
    arn_text: str,
    *,
    path: str,
    sha: str,
    span: SourceSpan | None,
    evi: int,
) -> DomainIdentity | None:
    """ARN ``iam::acct:role/x`` → ``iam_role`` com conta explícita."""
    parts = parse_arn(arn_text)
    if parts is None or not is_iam_role_arn(parts):
        return None
    role_name = parts.resource
    ident = sink.entity(
        kind=DomainEntityKind.IAM_ROLE,
        name=role_name.rsplit("/", 1)[-1],
        qualified_name=role_name,
        catalog=aws_catalog("iam", parts.account, parts.region),
        namespace="",
        path=path,
        span=span,
        content_hash=sha,
        attributes={
            "arn": parts.arn,
            "account": parts.account,
            "partition": parts.partition,
        },
        evidence=(evi,),
    )
    sink.bump("iam_roles")
    return ident


def _mark_used(call: ast.Call, used: set[int]) -> None:
    """Marca ``Constant`` str dentro dos args do call como consumidos."""
    for node in ast.walk(call):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            used.add(id(node))


def _merge_attrs(
    sink: Sink,
    ident: DomainIdentity,
    attrs: dict[str, str],
    *,
    path: str,
    sha: str,
    span: SourceSpan | None,
    evi: int,
) -> None:
    """Reemite a entidade já registrada acrescentando ``attrs``."""
    prev = sink.entities.get(ident.logical_key())
    if prev is None:
        return
    sink.entity_for(
        ident,
        kind=prev.kind,
        name=prev.name,
        path=path or prev.path,
        span=span or prev.span,
        content_hash=sha,
        attributes=attrs,
        evidence=(evi,),
    )


def _job_call_args(
    sink: Sink,
    facts: _PyFacts,
    job: DomainIdentity,
    *,
    path: str,
    sha: str,
    starts: tuple[int, ...],
    used: set[int],
) -> None:
    """``job.init(name, ctx, args={…})`` / ``job.commit()`` literais.

    Extrai nomes de opção + ``JOB_BOOKMARK_OPTION`` e refs ``s3://``
    declaradas nos args — valores sensíveis ficam ``[REDACTED]``.
    """
    for call in [*facts.job_inits, *facts.job_commits]:
        _job_call_one(sink, call, job, path=path, sha=sha, starts=starts, used=used)


def _job_call_one(
    sink: Sink,
    call: ast.Call,
    job: DomainIdentity,
    *,
    path: str,
    sha: str,
    starts: tuple[int, ...],
    used: set[int],
) -> None:
    """Um ``job.init``/``job.commit`` — args literais → attrs/refs."""
    span = node_span(starts, call)
    evi = sink.evi(path, sha, span, notes="glue-job-args")
    args_node = _kw(call, "args")
    if args_node is None:
        for a in call.args:
            if isinstance(a, ast.Dict):
                args_node = a
                break
    if args_node is None:
        return
    opts = _dict_lit(args_node)
    if opts is None:
        sink.diag(
            "dynamic-job-args",
            "warning",
            "job.init/run args is not a literal dict — option values unknown",
            path,
        )
        return
    attrs: dict[str, str] = {}
    for k, v in opts.items():
        _job_arg_entry(sink, job, k, v, attrs, path=path, sha=sha, span=span, evi=evi)
    if attrs:
        _merge_attrs(sink, job, attrs, path=path, sha=sha, span=span, evi=evi)
    _mark_used(call, used)


def _job_arg_entry(
    sink: Sink,
    job: DomainIdentity,
    k: object,
    v: object,
    attrs: dict[str, str],
    *,
    path: str,
    sha: str,
    span: SourceSpan | None,
    evi: int,
) -> None:
    """Uma entrada de ``args={…}``: attr redacted + bookmark + s3 ref."""
    kk = str(k).lstrip("-")
    if not kk:
        return
    attrs[f"job_arg.{kk}"] = redact_if_sensitive(kk, str(v))
    if kk == "JOB_BOOKMARK_OPTION" and isinstance(v, str):
        attrs["job_bookmarks"] = v
    if not isinstance(v, str) or parse_s3_uri(v) is None:
        return
    s3 = s3_entity(sink, v, path=path, sha=sha, span=span, evi=evi)
    if s3 is not None:
        sink.rel(_REFS, path, job, to_identity=s3, span=span, evidence=(evi,))
        sink.bump("s3_paths")


def _handle_call(
    sink: Sink,
    facts: _PyFacts,
    call: ast.Call,
    subject: DomainIdentity,
    *,
    path: str,
    text: str,
    sha: str,
    starts: tuple[int, ...],
    used: set[int],
) -> None:
    """Despacha um ``ast.Call`` para o reconhecedor adequado."""
    for disp in (
        _disp_glue_io,
        _disp_sqlish,
        _disp_sdk_calls,
        _disp_writes,
        _disp_reads,
    ):
        if disp(
            sink,
            facts,
            call,
            subject,
            path=path,
            text=text,
            sha=sha,
            starts=starts,
            used=used,
        ):
            return


def _disp_glue_io(
    sink: Sink,
    facts: _PyFacts,
    call: ast.Call,
    subject: DomainIdentity,
    *,
    path: str,
    sha: str,
    starts: tuple[int, ...],
    used: set[int],
    **_unused: object,
) -> bool:
    """``from_catalog``/``from_options``/``write_*`` do GlueContext."""
    dotted = _dotted(call.func)
    if dotted.endswith(_READ_CATALOG_SUFFIXES):
        _catalog_read(sink, call, subject, path=path, sha=sha, starts=starts)
    elif dotted.endswith(_WRITE_CATALOG_SUFFIXES):
        _catalog_write(sink, call, subject, path=path, sha=sha, starts=starts)
    elif dotted.endswith(_READ_OPTIONS_SUFFIXES):
        _connection_options(
            sink,
            call,
            subject,
            direction=_READS,
            path=path,
            sha=sha,
            starts=starts,
        )
    elif dotted.endswith(_WRITE_OPTIONS_SUFFIXES):
        _connection_options(
            sink,
            call,
            subject,
            direction=_WRITES,
            path=path,
            sha=sha,
            starts=starts,
        )
    else:
        return False
    _mark_used(call, used)
    return True


def _disp_sqlish(
    sink: Sink,
    facts: _PyFacts,
    call: ast.Call,
    subject: DomainIdentity,
    *,
    path: str,
    text: str,
    sha: str,
    starts: tuple[int, ...],
    used: set[int],
    **_unused: object,
) -> bool:
    """``X.sql(lit)``/``execute(lit)`` → SQL via ``sqlbridge``."""
    last = _dotted(call.func).rsplit(".", 1)[-1]
    arg: ast.AST | None = None
    notes = ""
    if last == "sql":
        arg = call.args[0] if call.args else _kw(call, "sqlQuery")
        # receiver spark OU literal que prova ser SQL — ambos honestos
        if not _is_spark_receiver(facts, call) and not (
            isinstance(arg, ast.Constant)
            and isinstance(arg.value, str)
            and looks_like_sql(arg.value)
        ):
            return True  # não-SQL — consumido e encerrado
        notes = "spark-sql-literal"
    elif last in ("execute", "executemany", "execute_query"):
        arg = call.args[0] if call.args else _kw(call, "query")
        if not (
            isinstance(arg, ast.Constant)
            and isinstance(arg.value, str)
            and looks_like_sql(arg.value)
        ):
            return True
        notes = "execute-sql"
    else:
        return False
    _sql_literal_call(
        sink,
        call,
        subject,
        arg_node=arg,
        path=path,
        text=text,
        sha=sha,
        starts=starts,
        notes=notes,
    )
    _mark_used(call, used)
    return True


def _disp_sdk_calls(
    sink: Sink,
    facts: _PyFacts,
    call: ast.Call,
    subject: DomainIdentity,
    *,
    path: str,
    text: str,
    sha: str,
    starts: tuple[int, ...],
    used: set[int],
    **_unused: object,
) -> bool:
    """``start_query_execution``/``create_job``/``update_job``."""
    last = _dotted(call.func).rsplit(".", 1)[-1]
    if last == "start_query_execution":
        _athena_call(
            sink,
            call,
            subject,
            path=path,
            text=text,
            sha=sha,
            starts=starts,
        )
    elif last in ("create_job", "update_job"):
        _glue_job_call(
            sink,
            call,
            subject,
            path=path,
            sha=sha,
            starts=starts,
            used=used,
        )
    else:
        return False
    _mark_used(call, used)
    return True


def _disp_writes(
    sink: Sink,
    facts: _PyFacts,
    call: ast.Call,
    subject: DomainIdentity,
    *,
    path: str,
    sha: str,
    starts: tuple[int, ...],
    used: set[int],
    **_unused: object,
) -> bool:
    """``writeTo``/``insertInto``/``saveAsTable`` → ``WRITES_TO``."""
    last = _dotted(call.func).rsplit(".", 1)[-1]
    if last == "writeTo":
        _write_to(sink, facts, call, subject, path=path, sha=sha, starts=starts)
    elif last in ("insertInto", "saveAsTable") and _is_spark_receiver(facts, call):
        _named_write(sink, call, subject, facts, path=path, sha=sha, starts=starts)
    else:
        return False
    _mark_used(call, used)
    return True


def _disp_reads(
    sink: Sink,
    facts: _PyFacts,
    call: ast.Call,
    subject: DomainIdentity,
    *,
    path: str,
    sha: str,
    starts: tuple[int, ...],
    used: set[int],
    **_unused: object,
) -> bool:
    """``spark.table``/``spark.read.<fmt>`` → ``READS_FROM``."""
    last = _dotted(call.func).rsplit(".", 1)[-1]
    if last == "table" and _is_spark_receiver(facts, call):
        _named_read(sink, call, subject, facts, path=path, sha=sha, starts=starts)
    elif last in _SPARK_READ_FMTS and _is_read_chain(facts, call):
        _read_path(sink, facts, call, subject, path=path, sha=sha, starts=starts)
    else:
        return False
    _mark_used(call, used)
    return True


def _chain_attrs(facts: _PyFacts, call: ast.Call) -> list[str]:
    """Atributos da cadeia englobante: ``w.writeTo(t).using(x).append()``
    → ``["writeTo", "using", "append"]`` (interno→externo)."""
    attrs: list[str] = []
    node: ast.AST = call
    seen = 0
    while seen < 16:
        parent = facts.parents.get(id(node))
        if parent is None:
            break
        if isinstance(parent, ast.Attribute):
            attrs.append(parent.attr)
            gp = facts.parents.get(id(parent))
            node = gp if gp is not None else parent
            if gp is None:
                break
        elif isinstance(parent, ast.Call):
            node = parent
        else:
            break
        seen += 1
    return attrs


def _chain_inner_calls(call: ast.Call) -> list[tuple[str, ast.Call]]:
    """Chamadas no receiver: ``a.read.format('x').load(t)`` →
    [(load,call),(format,…),(read,…)] externo→interno."""
    chain: list[tuple[str, ast.Call]] = []
    cur = call
    depth = 0
    while depth < 16:
        if not isinstance(cur.func, ast.Attribute):
            break
        chain.append((cur.func.attr, cur))
        recv = cur.func.value
        if not isinstance(recv, ast.Call):
            break
        cur = recv
        depth += 1
    return chain


def _is_read_chain(facts: _PyFacts, call: ast.Call) -> bool:
    """Terminal de ``<spark>.read.<fmt>(...)`` — receiver ``*.read``
    ou cadeia com ``.format(...)`` sobre receiver spark."""
    if not isinstance(call.func, ast.Attribute):
        return False
    recv = _dotted(call.func.value)
    if recv.endswith(".read") or recv == "read":
        return True
    for attr, _c in _chain_inner_calls(call):
        if attr in ("read", "format"):
            return _is_spark_receiver(facts, call)
    return False


def _chain_format(facts: _PyFacts, call: ast.Call) -> str:
    """Valor literal de ``.format("x")`` na cadeia receiver do call."""
    for attr, c in _chain_inner_calls(call):
        if attr == "format":
            v = _pos_lit(c, 0)
            if v:
                return v.lower()
    return ""


def _write_to(
    sink: Sink,
    facts: _PyFacts,
    call: ast.Call,
    subject: DomainIdentity,
    *,
    path: str,
    sha: str,
    starts: tuple[int, ...],
) -> None:
    """``df.writeTo("c.n.t")`` + ``.using("iceberg")`` + terminal."""
    outer = _chain_attrs(facts, call)
    terminal = [a for a in outer if a in _WRITE_TERMINALS]
    if not terminal:
        return  # builder sem operação terminal — nada a afirmar
    using_iceberg = False
    node: ast.AST = call
    for _ in range(16):
        parent = facts.parents.get(id(node))
        if isinstance(parent, ast.Attribute) and parent.attr == "using":
            gp = facts.parents.get(id(parent))
            if isinstance(gp, ast.Call):
                v = _pos_lit(gp, 0)
                if v and v.lower() in _ICEBERG_FORMATS:
                    using_iceberg = True
                    break
        if isinstance(parent, ast.Call):
            node = parent
            continue
        node = parent if parent is not None else node
        if parent is None:
            break
    written = _pos_lit(call, 0)
    span = node_span(starts, call)
    evi = sink.evi(path, sha, span, notes="writeTo")
    if not written:
        sink.diag(
            "dynamic-table-ref",
            "warning",
            "writeTo with non-literal target",
            path,
        )
        sink.rel(
            _WRITES,
            path,
            subject,
            to_unresolved="<dynamic>",
            status=ResolutionStatus.UNRESOLVED,
            span=span,
            evidence=(evi,),
        )
        return
    cat, _, _name = split_table_name(written)
    iceberg = using_iceberg or (
        cat in facts.declared_catalogs and is_iceberg_impl(facts.declared_catalogs[cat])
    )
    ident = _table_entity(
        sink,
        written,
        iceberg=iceberg,
        path=path,
        sha=sha,
        span=span,
        evi=evi,
    )
    if ident is None:
        return
    sink.rel(
        _WRITES,
        path,
        subject,
        to_identity=ident,
        span=span,
        evidence=(evi,),
    )
    _catalog_ref_for(sink, written, subject, path, span, evi)
    sink.bump("writes")


def _named_write(
    sink: Sink,
    call: ast.Call,
    subject: DomainIdentity,
    facts: _PyFacts,
    *,
    path: str,
    sha: str,
    starts: tuple[int, ...],
) -> None:
    """``insertInto``/``saveAsTable("n.t")`` → ``WRITES_TO``."""
    written = _pos_or_kw(call, 0, "tableName") or _kw_lit(call, "name")
    span = node_span(starts, call)
    evi = sink.evi(path, sha, span, notes="named-write")
    if not written:
        sink.rel(
            _WRITES,
            path,
            subject,
            to_unresolved="<dynamic>",
            status=ResolutionStatus.UNRESOLVED,
            span=span,
            evidence=(evi,),
        )
        sink.diag(
            "dynamic-table-ref",
            "warning",
            "non-literal write target",
            path,
        )
        return
    cat = split_table_name(written)[0]
    iceberg = cat in facts.declared_catalogs and is_iceberg_impl(facts.declared_catalogs[cat])
    ident = _table_entity(
        sink,
        written,
        iceberg=iceberg,
        path=path,
        sha=sha,
        span=span,
        evi=evi,
    )
    if ident is None:
        return
    sink.rel(
        _WRITES,
        path,
        subject,
        to_identity=ident,
        span=span,
        evidence=(evi,),
    )
    _catalog_ref_for(sink, written, subject, path, span, evi)
    sink.bump("writes")


def _named_read(
    sink: Sink,
    call: ast.Call,
    subject: DomainIdentity,
    facts: _PyFacts,
    *,
    path: str,
    sha: str,
    starts: tuple[int, ...],
) -> None:
    """``spark.table("c.n.t")`` → ``READS_FROM``."""
    written = _pos_lit(call, 0) or _kw_lit(call, "tableName")
    span = node_span(starts, call)
    evi = sink.evi(path, sha, span, notes="named-read")
    if not written:
        sink.rel(
            _READS,
            path,
            subject,
            to_unresolved="<dynamic>",
            status=ResolutionStatus.UNRESOLVED,
            span=span,
            evidence=(evi,),
        )
        return
    cat = split_table_name(written)[0]
    iceberg = cat in facts.declared_catalogs and is_iceberg_impl(facts.declared_catalogs[cat])
    ident = _table_entity(
        sink,
        written,
        iceberg=iceberg,
        path=path,
        sha=sha,
        span=span,
        evi=evi,
    )
    if ident is None:
        return
    sink.rel(
        _READS,
        path,
        subject,
        to_identity=ident,
        span=span,
        evidence=(evi,),
    )
    _catalog_ref_for(sink, written, subject, path, span, evi)
    sink.bump("reads")


def _read_path(
    sink: Sink,
    facts: _PyFacts,
    call: ast.Call,
    subject: DomainIdentity,
    *,
    path: str,
    sha: str,
    starts: tuple[int, ...],
) -> None:
    """``spark.read.<fmt>(path|table)`` — s3 → entidade; nome → tabela."""
    fmt = _dotted(call.func).rsplit(".", 1)[-1]
    val = _pos_lit(call, 0)
    span = node_span(starts, call)
    evi = sink.evi(path, sha, span, notes=f"read-{fmt}")
    fmt_is_iceberg = _chain_format(facts, call) in _ICEBERG_FORMATS
    if val is None:
        # lista literal de paths?
        arg0 = call.args[0] if call.args else None
        lst = _literal(arg0)
        if isinstance(lst, (list, tuple)):
            for v in lst:
                _read_path_value(
                    sink,
                    str(v),
                    facts,
                    subject,
                    fmt_is_iceberg,
                    path=path,
                    sha=sha,
                    span=span,
                    evi=evi,
                )
            return
        sink.rel(
            _READS,
            path,
            subject,
            to_unresolved="<dynamic>",
            status=ResolutionStatus.UNRESOLVED,
            span=span,
            evidence=(evi,),
        )
        sink.diag(
            "dynamic-path",
            "warning",
            f"spark.read.{fmt} with non-literal path",
            path,
        )
        return
    _read_path_value(
        sink,
        val,
        facts,
        subject,
        fmt_is_iceberg,
        path=path,
        sha=sha,
        span=span,
        evi=evi,
    )


def _read_path_value(
    sink: Sink,
    val: str,
    facts: _PyFacts,
    subject: DomainIdentity,
    iceberg: bool,
    *,
    path: str,
    sha: str,
    span: SourceSpan | None,
    evi: int,
) -> None:
    s3 = s3_entity(sink, val, path=path, sha=sha, span=span, evi=evi)
    if s3 is not None:
        sink.rel(
            _READS,
            path,
            subject,
            to_identity=s3,
            span=span,
            evidence=(evi,),
        )
        sink.bump("s3_paths")
        return
    if val.lower().startswith(("s3://", "s3a://", "s3n://")):
        sink.diag(
            "dynamic-s3-uri",
            "warning",
            f"s3 URI not statically decodable: {val[:80]}",
            path,
        )
        return
    if re.match(r"^[A-Za-z_][\w]*(?:\.[\w`\"]+)+$", val):
        ident = _table_entity(
            sink,
            val,
            iceberg=iceberg,
            path=path,
            sha=sha,
            span=span,
            evi=evi,
        )
        if ident is not None:
            sink.rel(
                _READS,
                path,
                subject,
                to_identity=ident,
                span=span,
                evidence=(evi,),
            )
            _catalog_ref_for(sink, val, subject, path, span, evi)
            sink.bump("reads")
            return
    sink.rel(
        _REFS,
        path,
        subject,
        to_unresolved=val[:200],
        status=ResolutionStatus.EXTERNAL,
        span=span,
        evidence=(evi,),
    )


def _athena_call(
    sink: Sink,
    call: ast.Call,
    subject: DomainIdentity,
    *,
    path: str,
    text: str,
    sha: str,
    starts: tuple[int, ...],
) -> None:
    """``start_query_execution(QueryString=..., QueryExecutionContext=…)``."""
    qctx = _dict_lit(_kw(call, "QueryExecutionContext"))
    catalog = schema = ""
    if qctx:
        catalog = str(qctx.get("Catalog") or "")
        schema = str(qctx.get("Database") or "")
    out = _dict_lit(_kw(call, "ResultConfiguration"))
    span = node_span(starts, call)
    if out:
        loc = out.get("OutputLocation")
        if isinstance(loc, str):
            evi = sink.evi(path, sha, span, notes="athena-output")
            s3 = s3_entity(sink, loc, path=path, sha=sha, span=span, evi=evi)
            if s3 is not None:
                sink.rel(
                    _WRITES,
                    path,
                    subject,
                    to_identity=s3,
                    span=span,
                    evidence=(evi,),
                )
    _sql_literal_call(
        sink,
        call,
        subject,
        arg_node=_kw(call, "QueryString"),
        path=path,
        text=text,
        sha=sha,
        starts=starts,
        use_catalog=catalog,
        use_schema=schema,
        notes="athena-querystring",
    )


def _glue_job_call(
    sink: Sink,
    call: ast.Call,
    subject: DomainIdentity,
    *,
    path: str,
    sha: str,
    starts: tuple[int, ...],
    used: set[int],
) -> None:
    """boto3 ``create_job``/``update_job`` com kwargs literais."""
    name = _kw_lit(call, "Name")
    span = node_span(starts, call)
    evi = sink.evi(path, sha, span, notes="glue-job-def-call")
    if not name:
        sink.diag(
            "dynamic-job-name",
            "info",
            "create_job/update_job without literal Name — no job entity minted",
            path,
        )
        return
    job = sink.entity(
        kind=DomainEntityKind.GLUE_JOB,
        name=name,
        qualified_name=name,
        namespace=posixpath.dirname(path),
        path=path,
        span=span,
        content_hash=sha,
        attributes={"artifact_kind": "boto3-create-job"},
        evidence=(evi,),
    )
    if subject.logical_key() != job.logical_key():
        sink.rel(
            "CONTAINS",
            path,
            subject,
            to_identity=job,
            span=span,
            evidence=(evi,),
        )
    _glue_call_role(sink, call, job, path=path, sha=sha, span=span, evi=evi, used=used)
    _glue_call_command(sink, call, job, name, path=path, sha=sha, span=span, evi=evi)
    _glue_call_defaults(sink, call, job, name, path=path, sha=sha, span=span, evi=evi)


def _glue_call_role(
    sink: Sink,
    call: ast.Call,
    job: DomainIdentity,
    *,
    path: str,
    sha: str,
    span: SourceSpan | None,
    evi: int,
    used: set[int],
) -> None:
    """``Role="arn:…:role/x"`` → ``iam_role`` + ``ASSUMES_ROLE`` external."""
    role = _kw(call, "Role")
    role_lit = _str_lit(role)
    if not role_lit or parse_arn(role_lit) is None:
        return
    role_ident = _role_entity(sink, role_lit, path=path, sha=sha, span=span, evi=evi)
    if role_ident is None:
        return
    sink.rel(
        _ASSUMES,
        path,
        job,
        to_identity=role_ident,
        status=ResolutionStatus.EXTERNAL,
        span=span,
        evidence=(evi,),
    )
    if isinstance(role, ast.Constant):
        used.add(id(role))


def _glue_call_command(
    sink: Sink,
    call: ast.Call,
    job: DomainIdentity,
    name: str,
    *,
    path: str,
    sha: str,
    span: SourceSpan | None,
    evi: int,
) -> None:
    """``Command={"Name","ScriptLocation"}`` → attr + ``REFERENCES`` s3."""
    cmd = _dict_lit(_kw(call, "Command"))
    if not cmd:
        return
    loc = cmd.get("ScriptLocation")
    if isinstance(loc, str):
        s3 = s3_entity(sink, loc, path=path, sha=sha, span=span, evi=evi)
        if s3 is not None:
            sink.rel(
                _REFS,
                path,
                job,
                to_identity=s3,
                span=span,
                evidence=(evi,),
            )
    cname = cmd.get("Name")
    if isinstance(cname, str):
        # atributo pós-criação: reemite entidade com merge honesto
        sink.entity_for(
            job,
            kind=DomainEntityKind.GLUE_JOB,
            name=name,
            path=path,
            span=span,
            content_hash=sha,
            attributes={
                "artifact_kind": "boto3-create-job",
                "command": cname,
            },
            evidence=(evi,),
        )


def _glue_call_defaults(
    sink: Sink,
    call: ast.Call,
    job: DomainIdentity,
    name: str,
    *,
    path: str,
    sha: str,
    span: SourceSpan | None,
    evi: int,
) -> None:
    """``DefaultArguments`` → attrs ``arg.*`` redacted + refs ``s3://``."""
    defaults = _dict_lit(_kw(call, "DefaultArguments"))
    if not defaults:
        return
    extra: dict[str, str] = {}
    for k, v in defaults.items():
        extra[f"arg.{k}"] = redact_if_sensitive(k, str(v))
        if isinstance(v, str) and parse_s3_uri(v) is not None:
            s3 = s3_entity(sink, v, path=path, sha=sha, span=span, evi=evi)
            if s3 is not None:
                sink.rel(
                    _REFS,
                    path,
                    job,
                    to_identity=s3,
                    span=span,
                    evidence=(evi,),
                )
    if extra:
        sink.entity_for(
            job,
            kind=DomainEntityKind.GLUE_JOB,
            name=name,
            path=path,
            span=span,
            content_hash=sha,
            attributes={"artifact_kind": "boto3-create-job", **extra},
            evidence=(evi,),
        )


def _sweep_literals(
    sink: Sink,
    facts: _PyFacts,
    subject: DomainIdentity,
    *,
    path: str,
    sha: str,
    starts: tuple[int, ...],
    used: set[int],
) -> None:
    """Varredura final: literais ``s3://``/ARN não consumidos."""
    for node in facts.str_nodes:
        if id(node) in used:
            continue
        val = node.value
        if not isinstance(val, str):
            continue
        span = node_span(starts, node)
        if parse_s3_uri(val) is not None:
            evi = sink.evi(path, sha, span, notes="s3-literal")
            s3 = s3_entity(sink, val, path=path, sha=sha, span=span, evi=evi)
            if s3 is not None:
                sink.rel(
                    _REFS,
                    path,
                    subject,
                    to_identity=s3,
                    span=span,
                    evidence=(evi,),
                )
                sink.bump("s3_paths")
            continue
        for m in ARN_RE.finditer(val):
            parts = parse_arn(m.group(0))
            if parts is None or not is_iam_role_arn(parts):
                continue
            evi = sink.evi(path, sha, span, notes="iam-arn-literal")
            role = _role_entity(
                sink,
                m.group(0),
                path=path,
                sha=sha,
                span=span,
                evi=evi,
            )
            if role is not None:
                sink.rel(
                    _REFS,
                    path,
                    subject,
                    to_identity=role,
                    span=span,
                    evidence=(evi,),
                )


def _degraded_lits(text: str, starts: tuple[int, ...]) -> list[tuple[str, SourceSpan | None]]:
    """Literais str coletados por ``tokenize`` até a quebra."""
    lits: list[tuple[str, SourceSpan | None]] = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        for tok in tokens:
            if tok.type != tokenize.STRING:
                continue
            try:
                val = ast.literal_eval(tok.string)
            except (ValueError, SyntaxError):
                continue
            if isinstance(val, str):
                lits.append((val, _token_span(starts, tok.start, tok.end)))
    except tokenize.TokenError:
        pass  # degradado — o que foi coletado até a quebra vale
    return lits


def _emit_degraded_lit(
    sink: Sink,
    val: str,
    span: SourceSpan | None,
    job: DomainIdentity,
    *,
    path: str,
    sha: str,
) -> None:
    """Um literal degradado → entidade s3 ou ``iam_role`` + REFERENCES."""
    if parse_s3_uri(val) is not None:
        evi = sink.evi(path, sha, span, notes="s3-literal")
        s3 = s3_entity(sink, val, path=path, sha=sha, span=span, evi=evi)
        if s3 is not None:
            sink.rel(
                _REFS,
                path,
                job,
                to_identity=s3,
                span=span,
                evidence=(evi,),
            )
        return
    for m in ARN_RE.finditer(val):
        parts = parse_arn(m.group(0))
        if parts is None or not is_iam_role_arn(parts):
            continue
        evi = sink.evi(path, sha, span, notes="iam-arn")
        role = _role_entity(sink, m.group(0), path=path, sha=sha, span=span, evi=evi)
        if role is not None:
            sink.rel(
                _REFS,
                path,
                job,
                to_identity=role,
                span=span,
                evidence=(evi,),
            )


def _degraded_scan(
    sink: Sink,
    *,
    path: str,
    text: str,
    sha: str,
    starts: tuple[int, ...],
) -> None:
    """``SyntaxError`` → varredura tokenize de literais (degradado)."""
    lits = _degraded_lits(text, starts)
    s3_count = sum(1 for v, _ in lits if parse_s3_uri(v) is not None)
    arn_count = sum(1 for v, _ in lits if ARN_RE.search(v))
    if not s3_count and not arn_count:
        return
    stem = PurePosixPath(path).stem
    kind = (
        DomainEntityKind.GLUE_JOB
        if "awsglue" in text or "GlueContext" in text
        else DomainEntityKind.DATA_PIPELINE
    )
    job = sink.entity(
        kind=kind,
        name=stem,
        qualified_name=stem,
        namespace=posixpath.dirname(path),
        path=path,
        span=None,
        content_hash=sha,
        attributes={
            "artifact_kind": "py-script",
            "degraded_scan": "tokenize-fallback",
        },
        evidence=(sink.evi(path, sha, None, notes="degraded"),),
    )
    for val, span in lits:
        _emit_degraded_lit(sink, val, span, job, path=path, sha=sha)


def _token_span(
    starts: tuple[int, ...],
    start: tuple[int, int],
    end: tuple[int, int],
) -> SourceSpan | None:
    """(lineno, col-char) de tokenize → SourceSpan bytes."""
    from code_atlas.domains.aws.common import byte_span

    def _abs(pos: tuple[int, int]) -> int | None:
        line, col = pos
        if line < 1 or line > len(starts):
            return None
        return starts[line - 1] + col

    b0, b1 = _abs(start), _abs(end)
    if b0 is None or b1 is None:
        return None
    return byte_span(starts, b0, b1)
