"""Avaliador estático de DataFrames PySpark sobre ``ast`` (lf-0112, Ph.4).

Nenhum parser regex, nenhuma execução: o walker percorre o ``ast`` do
``.py`` em ordem sequencial mantendo um **ambiente de bindings** —
``nome → sessão | dataframe | writer | str-literal | não-df |
desconhecido`` — com escopo por função e merge de branches em
``if``/``match`` (loops e ``try`` usam aproximação sequencial
declarada: corpo desenrolado uma vez).

Modelo de linhagem (espelha a semântica do adapter ``sql``):

- **transformação materializada** (``data_transformation``): criada
  quando um valor-DataFrame ancora um nome (``df = …``) ou uma
  expressão terminal executa (``spark.sql``, ``df.write…``, dfv
  "complexo" achado em argumento de função desconhecida);
- ``T READS_FROM tabela|path``: ``spark.read.*``, ``spark.table``,
  ``spark.sql(SELECT …)``;
- ``T DERIVES_FROM T'``: encadeamento var→var (``enriched = df.join…``);
- ``T DERIVES_FROM to_unresolved``: receiver/arg não-resolvível —
  linhagem **parcial**, honesta, diagnosticada;
- ``T WRITES_TO tabela|path|iceberg``: ``df.write.*``,
  ``df.writeTo(t).<terminal>``, ``spark.sql(INSERT/CREATE …)``;
- ``T TRANSFORMS tabela``: mutação in-place vinda de literal
  ``spark.sql`` (UPDATE/DELETE/MERGE — remap do adapter sql);
- ``sink DERIVES_FROM fonte``: colapso tabela→tabela do write
  (mesmo desenho do CTAS no adapter sql).

Regras de honestidade (hard):

- variável-DataFrame NÃO é tabela persistida — ``data_transformation``
  âncora ``<arquivo>#<var>@<linha>`` nunca colide com identidade de
  ``data_table`` (``catalog.namespace.name``);
- paths (``s3://…``) viram ``data_table`` com ``storage="path"`` e
  ``qualified_name`` = literal verbatim — nunca conflitam com tabelas
  de catálogo;
- ``spark.sql(não-literal)`` → ``dynamic-sql`` + ``to_unresolved`` —
  nenhuma dependência fabricada;
- receiver desconhecido → ``unresolved-dataframe`` (info) + aresta
  ``to_unresolved`` — parcial, nunca fabricado;
- literais de string propagam apenas via constantes ``str`` do próprio
  arquivo (``TABLE = "db.t"``); f-strings com ``{}`` são dinâmicas;
- ``.py`` sem uso de Spark → ignorado em silêncio (stats somente).
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from code_atlas.contracts.enums import EdgeKind
from code_atlas.domains.identity import DomainIdentity
from code_atlas.domains.model import DomainEntityKind, DomainRelationKind
from code_atlas.domains.spark.emit import (
    DOMAIN_ID,
    Sink,
    split_table_name,
    uri_scheme,
)
from code_atlas.domains.spark.sqllit import delegate_sql_literal
from code_atlas.parsers.contracts import SourceSpan

# -- vocabulário PySpark reconhecido (estático, declarado) -------------------

_SESSION_NAMES = frozenset({"spark", "spark_session", "sparkSession"})
_SESSION_ATTRS = frozenset({"spark", "spark_session"})  # self.spark
_SESSION_READ_ATTRS = frozenset({"read", "readStream"})
_SESSION_METHODS = frozenset(
    {
        "read", "readStream", "sql", "table", "createDataFrame", "range",
        "newSession", "getActiveSession", "sparkContext", "conf",
        "catalog", "udf", "streams", "version", "stop",
    }
)
# Métodos de sessão que definitivamente NÃO devolvem DataFrame.
_SESSION_NON_DF = frozenset(
    {
        "sparkContext", "conf", "catalog", "udf", "streams", "version",
        "stop", "getActiveSession", "newSession",
    }
)

# Operações DataFrame→DataFrame (spec: select/filter/join/groupBy/agg/
# union/withColumn/dropDuplicates/withColumnRenamed + equivalentes da
# API pública documentada).
_DF_TRANSFORMS = frozenset(
    {
        "select", "selectExpr", "filter", "where", "join", "crossJoin",
        "joinWith", "union", "unionByName", "unionAll",
        "intersect", "intersectAll", "exceptAll", "subtract",
        "groupBy", "agg", "rollup", "cube",
        "withColumn", "withColumns", "withColumnRenamed",
        "withColumnsRenamed", "withMetadata", "withField",
        "drop", "dropDuplicates", "drop_duplicates",
        "dropDuplicatesWithinWatermark", "distinct",
        "orderBy", "sort", "sortWithinPartitions", "limit", "alias",
        "repartition", "repartitionByRange", "coalesce",
        "cache", "persist", "unpersist", "checkpoint", "localCheckpoint",
        "dropna", "fillna", "replace", "sample", "sampleBy",
        "transform", "toDF", "toJSON", "describe", "summary",
        "hint", "observe", "withWatermark", "mapInPandas",
        "applyInPandas",
    }
)
# Transformações cujo primeiro argumento também é um DataFrame.
_DF_ARG_OPS = frozenset(
    {
        "join", "crossJoin", "joinWith", "union", "unionByName",
        "unionAll", "exceptAll", "subtract", "intersect", "intersectAll",
    }
)
# Chamadas que definitivamente NÃO devolvem DataFrame (ações/terminais).
_DF_NON_DF_CALLS = frozenset(
    {
        "collect", "count", "show", "take", "head", "first", "tail",
        "foreach", "foreachPartition", "reduce", "explain",
        "printSchema", "toPandas", "toLocalIterator", "inputFiles",
        "isLocal", "isEmpty", "countApprox", "approxQuantile",
        "randomSplit", "countByValue",
    }
)
_DF_NON_DF_ATTRS = frozenset(
    {"columns", "schema", "dtypes", "rdd", "isStreaming"}
)

# Terminais de ``df.write.*`` que escrevem em PATH.
_WRITER_PATH_TERMINALS = frozenset(
    {"parquet", "csv", "json", "orc", "text", "avro", "save"}
)
# Terminais de ``df.write.*`` que escrevem em TABELA de catálogo.
_WRITER_TABLE_TERMINALS = frozenset({"saveAsTable", "insertInto", "jdbc"})
_WRITER_OPTS = frozenset(
    {"mode", "format", "option", "options", "partitionBy",
     "bucketBy", "sortBy"}
)
# Terminais de ``df.writeTo(t)`` (DataFrameWriterV2 — Iceberg-style).
_V2_TERMINALS = frozenset(
    {"append", "overwrite", "overwritePartitions", "replace",
     "createOrReplace", "create"}
)
_V2_OPTS = frozenset(
    {"using", "option", "options", "tableProperty", "partitionedBy",
     "clusteredBy"}
)
# Temp views — ``df.createOrReplaceTempView("v")`` etc.
_TEMP_VIEW_METHODS = frozenset(
    {
        "createOrReplaceTempView", "createTempView",
        "createOrReplaceGlobalTempView", "createGlobalTempView",
        "registerTempTable",
    }
)
_NA_METHODS = frozenset({"drop", "fill", "replace"})
_TRANSPARENT_FNS = frozenset({"broadcast"})
# Formatos de path do DataFrameReader (``spark.read.<fmt>(path)``).
_READER_PATH_FORMATS = frozenset(
    {"parquet", "csv", "json", "orc", "text", "avro"}
)

# Gate fraco do arquivo: ops camelCase distintivas da API DataFrame
# (``.filter``/``.select`` minúsculos ficam de fora — pandas colide).
_CAMEL_DF_OPS = frozenset(
    {
        "groupBy", "agg", "rollup", "cube", "withColumn", "withColumns",
        "withColumnRenamed", "withColumnsRenamed", "dropDuplicates",
        "drop_duplicates", "dropDuplicatesWithinWatermark",
        "unionByName", "unionAll", "selectExpr", "repartition",
        "repartitionByRange", "coalesce", "withWatermark", "joinWith",
        "writeTo", "saveAsTable", "insertInto",
        "createOrReplaceTempView", "createGlobalTempView",
        "createTempView", "registerTempTable", "exceptAll",
        "intersectAll", "localCheckpoint", "foreachBatch",
    }
)


# -- valores avaliados --------------------------------------------------------


class _E:
    """União de resultados da avaliação estática de expressão."""


class _Session(_E):
    """SparkSession (ou objeto que produz uma)."""


class _SClass(_E):
    """Referência à classe ``SparkSession`` (ou alias importado)."""


class _SBuilder(_E):
    """``SparkSession.builder[.appName…]`` — vira sessão no getOrCreate."""


@dataclass
class _Reader(_E):
    """``spark.read[.format(f)][.option…]`` antes do terminal."""

    fmt: str = ""


@dataclass
class _Str(_E):
    """Constante ``str`` — propagação honesta de literal por variável."""

    value: str


@dataclass
class _Read:
    """Alvo de leitura observado (identidade emitida na materialização)."""

    ident_key: str       # logical_key da entidade (tabela|path|view)
    fmt: str             # parquet|csv|json|table|jdbc|iceberg|…
    span: SourceSpan | None = None


@dataclass
class _PKey:
    """Pai materializado: variável já ligada a uma transformação."""

    ident_key: str


@dataclass
class _PAnon:
    """Pai anônimo: dfv intermediário dentro da mesma expressão."""

    dfv: _Dfv


@dataclass
class _PUnres:
    """Pai não-resolvível — ``to_unresolved`` honesto."""

    text: str
    span: SourceSpan | None = None


_Parent = _PKey | _PAnon | _PUnres


@dataclass
class _SqlLit:
    """Literal ``spark.sql(…)`` — delegado ao adapter sql na materialização."""

    literal: str
    span: SourceSpan | None
    line: int


@dataclass
class _Dfv(_E):
    """Resumo estático de um valor-DataFrame (ainda não materializado)."""

    reads: list[_Read] = field(default_factory=list)
    parents: list[_Parent] = field(default_factory=list)
    sql: list[_SqlLit] = field(default_factory=list)

    def is_trivial(self) -> bool:
        """Alias puro ``x = df`` — rebind sem nova transformação."""
        return (
            not self.reads
            and not self.sql
            and len(self.parents) == 1
            and isinstance(self.parents[0], _PKey)
        )

    def is_interesting(self) -> bool:
        """Merece materialização própria quando achado solto."""
        if self.reads or self.sql:
            return True
        return any(not isinstance(p, _PKey) for p in self.parents)


@dataclass
class _Writer(_E):
    """``<df>.write[.mode][.format…]`` — aguarda terminal."""

    dfv: _Dfv | None
    unknown_recv: str = ""
    mode: str = ""
    fmt: str = ""
    span: SourceSpan | None = None


@dataclass
class _WriterV2(_E):
    """``<df>.writeTo(t)[.using…]`` — aguarda terminal V2."""

    dfv: _Dfv | None
    unknown_recv: str = ""
    target_lit: str | None = None
    target_span: SourceSpan | None = None
    via: str = ""
    span: SourceSpan | None = None


@dataclass
class _NaOps(_E):
    """``df.na`` — ``.drop()/.fill()/.replace()`` devolvem DataFrame."""

    dfv: _Dfv


class _NotDf(_E):
    """Expressão conhecida não-DataFrame (ação, collection, writer…)."""


@dataclass
class _Unknown(_E):
    """Expressão não-classificada — ops DataFrame nela viram parcial."""

    text: str
    span: SourceSpan | None = None


@dataclass
class _SeqE(_E):
    """Tupla/lista literal avaliada — unpack de assignment."""

    items: list[_E] = field(default_factory=list)


# -- bindings do ambiente -----------------------------------------------------


class _Bind:
    """União de bindings nomeados em um escopo."""


class _BSession(_Bind):
    pass


@dataclass
class _BDf(_Bind):
    ident_key: str


@dataclass
class _BWriter(_Bind):
    writer: _E  # _Writer | _WriterV2


class _BNotDf(_Bind):
    pass


class _BUnknown(_Bind):
    pass


@dataclass
class _BStr(_Bind):
    """Binding de constante ``str`` — propagada como literal."""

    value: str


@dataclass
class _BParamDf(_Bind):
    """Parâmetro anotado ``DataFrame`` — fonte opaca, honesta."""

    name: str


class _Env:
    """Pilha de escopos ``nome → _Bind`` (o mais interno escreve)."""

    def __init__(self) -> None:
        self.scopes: list[dict[str, _Bind]] = [{}]

    def push(self) -> None:
        self.scopes.append({})

    def pop(self) -> dict[str, _Bind]:
        return self.scopes.pop()

    def lookup(self, name: str) -> _Bind | None:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return None

    def bind(self, name: str, b: _Bind) -> None:
        self.scopes[-1][name] = b

    def unbind(self, name: str) -> None:
        self.scopes[-1][name] = _BUnknown()


# -- helpers de AST -----------------------------------------------------------


def _attr_chain(node: ast.expr) -> tuple[str | None, list[str]]:
    """Desenrola ``a.b.c.d`` → (``a`` como Name|None, [b,c,d])."""
    attrs: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        attrs.append(cur.attr)
        cur = cur.value
    attrs.reverse()
    return (cur.id if isinstance(cur, ast.Name) else None), attrs


def _literal_str(node: ast.expr, *, depth: int = 0) -> str | None:
    """Literal de string estático; ``None`` quando dinâmico.

    Cobre ``"x"``, concatenação ``"a" + "b"`` e f-string **sem**
    placeholders. ``JoinedStr`` com ``FormattedValue`` → ``None``
    (dinâmico — nunca fabricado).
    """
    if depth > 8:
        return None
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
            else:
                return None
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_str(node.left, depth=depth + 1)
        right = _literal_str(node.right, depth=depth + 1)
        if left is not None and right is not None:
            return left + right
    return None


def _ann_name(node: ast.expr | None) -> str:
    """Nome terminal de uma anotação (``pyspark.sql.DataFrame`` → DataFrame)."""
    if node is None:
        return ""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.Subscript):
        return _ann_name(node.value)
    return ""


@dataclass
class _Flat:
    """Resultado do flatten de um ``_Dfv`` (pais anônimos expandidos)."""

    reads: list[_Read] = field(default_factory=list)
    pkeys: list[str] = field(default_factory=list)
    unres: list[tuple[str, SourceSpan | None]] = field(default_factory=list)
    sql: list[_SqlLit] = field(default_factory=list)


# -- walker --------------------------------------------------------------------


class _Walker:
    """Percorre o ``ast`` de um ``.py`` emitindo fatos no ``Sink``."""

    def __init__(
        self, sink: Sink, path: str, sha: str, text: str
    ) -> None:
        self.sink = sink
        self.path = path
        self.sha = sha
        self.text = text
        self.temp_views: dict[str, str] = {}  # view name → ident_key
        self.transform_idents: list[DomainIdentity] = []
        self.app_name = ""
        self.sparky = False
        self.implicit_sessions: set[str] = set()
        self.spark_classes: set[str] = set()
        self._offs = self._line_offs(text)

    # -- spans ----------------------------------------------------------------

    @staticmethod
    def _line_offs(text: str) -> list[int]:
        offs = [0]
        total = 0
        for raw in text.splitlines(keepends=True):
            total += len(raw.encode("utf-8"))
            offs.append(total)
        return offs

    def _span(self, node: ast.AST) -> SourceSpan | None:
        lineno = getattr(node, "lineno", 0)
        col = getattr(node, "col_offset", 0)
        if not lineno or lineno > len(self._offs):
            return None
        end_line = getattr(node, "end_lineno", None) or lineno
        end_col = getattr(node, "end_col_offset", None) or col
        end_line = min(end_line, len(self._offs))
        return SourceSpan(
            start_byte=self._offs[lineno - 1] + col,
            end_byte=self._offs[end_line - 1] + end_col,
            start_line=lineno, start_col=col,
            end_line=end_line, end_col=end_col,
        )

    def _txt(self, node: ast.AST) -> str:
        try:
            txt = ast.unparse(node)
        except Exception:
            return "<expr>"
        txt = " ".join(txt.split())
        return txt[:120]

    def _literal_arg(self, node: ast.expr, env: _Env) -> str | None:
        """Literal estático do argumento — direto ou via binding ``str``."""
        lit = _literal_str(node)
        if lit is not None:
            return lit
        if isinstance(node, ast.Name):
            b = env.lookup(node.id)
            if isinstance(b, _BStr):
                return b.value
        return None

    # -- ponto de entrada -------------------------------------------------------

    def run(self) -> None:
        try:
            tree = ast.parse(self.text)
        except (SyntaxError, ValueError, RecursionError) as exc:
            self.sink.diag(
                "parse-error", "error",
                f"python ast.parse failed: {exc}", self.path,
            )
            self.sink.bump("parse_errors")
            return
        self._prescan(tree)
        if not self.sparky:
            self.sink.bump("non_spark_files")
            return
        env = _Env()
        for name in sorted(self.implicit_sessions):
            env.bind(name, _BSession())
        self.sink.bump("spark_files")
        self._stmts(tree.body, env)
        self._emit_job()

    def _emit_job(self) -> None:
        """``spark_job`` do artefato — âncora do pipeline + CONTAINS.

        A entidade-job é o alvo semântico para vínculos de provisiona-
        mento cross-domain (``aws_glue_job.script_location`` ↔ arquivo,
        lf-0117) e o container honesto das transformações do arquivo.

        ``import pyspark`` sozinho NÃO basta — sem transformação/view o
        arquivo declara dependência, não um job.
        """
        if not self.transform_idents and not self.temp_views:
            return
        stem = self.path[:-3] if self.path.endswith(".py") else self.path
        req = self.sink.request
        ident = DomainIdentity(
            domain=DOMAIN_ID, repository=req.repository, root=req.root,
            environment=req.environment, qualified_name=stem,
        )
        evi = self.sink.evi(self.path, self.sha, None, notes="spark-job")
        attrs = {"entry_point": self.path, "language": "python"}
        if self.app_name:
            attrs["app_name"] = self.app_name
        self.sink.add_entity(
            ident, DomainEntityKind.SPARK_JOB, stem,
            path=self.path, content_hash=self.sha,
            attributes=attrs, roles=("job",), evidence=(evi,),
        )
        for t in self.transform_idents:
            self.sink.add_rel(
                str(EdgeKind.CONTAINS), ident,
                path=self.path, to_i=t, evidence=(evi,),
            )
        self.sink.bump("spark_jobs")

    # -- gate de arquivo --------------------------------------------------------

    def _prescan(self, tree: ast.AST) -> None:
        """Decide se o arquivo usa Spark — sem custo de emitir nada.

        Marcadores: imports ``pyspark*``, nome ``SparkSession``, padrões
        ``<nome>.read.<fmt>`` (sessão implícita de notebook) e ops
        camelCase distintivas da API DataFrame (``.writeTo``,
        ``.write.parquet``, ``groupBy`` …).
        """
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self._prescan_importfrom(node)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name == "pyspark" or a.name.startswith("pyspark."):
                        self.sparky = True
            elif isinstance(node, ast.Name) and node.id == "SparkSession":
                self.sparky = True
            elif isinstance(node, ast.Call) and isinstance(
                node.func, ast.Attribute
            ):
                self._prescan_call(node)

    def _prescan_importfrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        if not (mod == "pyspark" or mod.startswith("pyspark.")):
            return
        self.sparky = True
        for a in node.names:
            if a.name == "SparkSession":
                self.spark_classes.add(a.asname or a.name)

    def _prescan_call(self, node: ast.Call) -> None:
        """Marcadores de ``<recv>.<cadeia>(…)`` no gate do arquivo."""
        base, attrs = _attr_chain(node.func)
        if not attrs:
            return
        if base in _SESSION_NAMES and attrs[0] in _SESSION_METHODS:
            self.sparky = True
            if base is not None:
                self.implicit_sessions.add(base)
        elif attrs[0] in _SESSION_READ_ATTRS and len(attrs) >= 2:
            # ``x.read.table(…)`` — leitor só existe em sessão
            self.sparky = True
            if base is not None:
                self.implicit_sessions.add(base)
        if (
            attrs[0] in ("write", "writeStream")
            and attrs[-1]
            in (_WRITER_PATH_TERMINALS | _WRITER_TABLE_TERMINALS)
        ):
            self.sparky = True
        if attrs[-1] in _CAMEL_DF_OPS:
            self.sparky = True

    # -- statements -------------------------------------------------------------

    def _stmts(self, body: list[ast.stmt], env: _Env) -> None:
        for st in body:
            self._stmt(st, env)

    def _stmt(self, st: ast.stmt, env: _Env) -> None:
        if isinstance(st, ast.Assign):
            value = self._eval(st.value, env)
            bound = self._bind_targets(st.targets, value, env)
            if not bound:
                self._after_eval(value, st)
        elif isinstance(st, ast.AnnAssign):
            if st.value is not None:
                value = self._eval(st.value, env)
                bound = self._bind_targets([st.target], value, env)
                if not bound:
                    self._after_eval(value, st)
        elif isinstance(st, ast.AugAssign):
            self._eval(st.value, env)
            if isinstance(st.target, ast.Name):
                env.unbind(st.target.id)
        elif isinstance(st, ast.Expr):
            self._after_eval(self._eval(st.value, env), st)
        else:
            handler = _STMT_DISPATCH.get(type(st))
            if handler is not None:
                handler(self, st, env)
        # Import/ImportFrom/Global/Nonlocal/Pass/Break/Continue — nada

    # -- handlers de statement (``_STMT_DISPATCH``) ---------------------------

    def _st_class(self, st: ast.ClassDef, env: _Env) -> None:
        for d in st.decorator_list:
            self._after_eval(self._eval(d, env), d)
        for b in st.bases:
            self._after_eval(self._eval(b, env), b)
        env.push()
        self._stmts(st.body, env)
        env.pop()

    def _st_if(self, st: ast.If, env: _Env) -> None:
        self._after_eval(self._eval(st.test, env), st.test)
        self._branches([st.body, st.orelse], env, st.lineno)

    def _st_for(self, st: ast.For | ast.AsyncFor, env: _Env) -> None:
        self._after_eval(self._eval(st.iter, env), st.iter)
        self._bind_loop_target(st.target, env)
        # aproximação sequencial declarada: corpo desenrolado 1x
        self._stmts(st.body, env)
        self._stmts(st.orelse, env)

    def _st_while(self, st: ast.While, env: _Env) -> None:
        self._after_eval(self._eval(st.test, env), st.test)
        self._stmts(st.body, env)
        self._stmts(st.orelse, env)

    def _st_with(self, st: ast.With | ast.AsyncWith, env: _Env) -> None:
        for item in st.items:
            v = self._eval(item.context_expr, env)
            if item.optional_vars is not None:
                self._bind_targets([item.optional_vars], v, env)
        self._stmts(st.body, env)

    def _st_try(self, st: ast.Try | ast.TryStar, env: _Env) -> None:
        self._stmts(st.body, env)
        for h in st.handlers:
            self._stmts(h.body, env)
        self._stmts(st.orelse, env)
        self._stmts(st.finalbody, env)

    def _st_return(self, st: ast.Return, env: _Env) -> None:
        if st.value is not None:
            self._after_eval(self._eval(st.value, env), st.value)

    def _st_raise(self, st: ast.Raise, env: _Env) -> None:
        if st.exc is not None:
            self._after_eval(self._eval(st.exc, env), st.exc)

    def _st_delete(self, st: ast.Delete, env: _Env) -> None:
        for t in st.targets:
            if isinstance(t, ast.Name):
                env.unbind(t.id)

    def _st_match(self, st: ast.Match, env: _Env) -> None:
        self._after_eval(self._eval(st.subject, env), st.subject)
        # match pode ser não-exaustivo — trilha vazia conta
        self._branches([c.body for c in st.cases] + [[]], env, st.lineno)

    def _st_assert(self, st: ast.Assert, env: _Env) -> None:
        self._after_eval(self._eval(st.test, env), st.test)

    def _branches(
        self, bodies: list[list[ast.stmt]], env: _Env, lineno: int
    ) -> None:
        """Processa branches em escopos-filho e funde bindings.

        Cada branch pode vincular o mesmo nome a transformações
        diferentes — o merge cria uma transformação
        ``<nome>@merge@<linha>`` que DERIVES_FROM todas as alternativas,
        nunca descartando uma leitura. Branch que não vincula o nome
        mantém o binding do escopo pai como candidato.
        """
        childs: list[dict[str, _Bind]] = []
        for body in bodies:
            env.push()
            self._stmts(body, env)
            childs.append(env.pop())
        parent = env.scopes[-1]
        names: list[str] = []
        for ch in childs:
            for n in ch:
                if n not in names:
                    names.append(n)
        for name in names:
            candidates: list[_Bind | None] = [
                ch[name] for ch in childs if name in ch
            ]
            covered = sum(1 for ch in childs if name in ch)
            if covered < len(childs) and name in parent:
                candidates.append(parent[name])
            merged = self._merge_binding(name, candidates, lineno)
            if merged is not None:
                parent[name] = merged
            elif name in parent:
                del parent[name]

    def _merge_binding(
        self, name: str, candidates: list[_Bind | None], lineno: int
    ) -> _Bind | None:
        """Fusão de bindings de branches — honesta, nunca resolved-na-sorte."""
        bound = [b for b in candidates if b is not None]
        if not bound:
            return None
        dfkeys: list[str] = []
        for b in bound:
            if isinstance(b, _BDf) and b.ident_key not in dfkeys:
                dfkeys.append(b.ident_key)
        if len(dfkeys) == 1:
            return _BDf(dfkeys[0])
        if len(dfkeys) > 1:
            dfv = _Dfv(parents=[_PKey(k) for k in dfkeys])
            ident = self._materialize(
                dfv, f"{name}@merge@{lineno}", None
            )
            return _BDf(ident.logical_key())
        for cls in (_BSession, _BWriter, _BParamDf):
            for b in bound:
                if isinstance(b, cls):
                    return b
        if any(isinstance(b, _BUnknown) for b in bound):
            return _BUnknown()
        return bound[-1]

    # -- assignments -------------------------------------------------------------

    def _bind_targets(
        self, targets: list[ast.expr], value: _E, env: _Env
    ) -> bool:
        """Liga RHS avaliado aos targets. True quando algo vinculou."""
        bound_any = False
        for t in targets:
            if isinstance(t, ast.Name):
                self._bind_name(t.id, value, env, t)
                bound_any = True
            elif isinstance(t, ast.Starred) and isinstance(
                t.value, ast.Name
            ):
                env.bind(t.value.id, _BUnknown())
                bound_any = True
            elif isinstance(t, (ast.Tuple, ast.List)):
                if isinstance(value, _SeqE) and len(t.elts) == len(
                    value.items
                ):
                    for el, item in zip(
                        t.elts, value.items, strict=True
                    ):
                        if isinstance(el, ast.Name):
                            self._bind_name(el.id, item, env, el)
                            bound_any = True
                else:
                    for el in t.elts:
                        if isinstance(el, ast.Name):
                            env.bind(el.id, _BUnknown())
                            bound_any = True
            # Attribute/Subscript targets — fora do env de nomes; o
            # caller materializa o valor via ``_after_eval``.
        return bound_any

    def _bind_loop_target(self, target: ast.expr, env: _Env) -> None:
        for t in ast.walk(target):
            if isinstance(t, ast.Name):
                env.bind(t.id, _BUnknown())

    def _bind_name(
        self, name: str, value: _E, env: _Env, node: ast.expr
    ) -> None:
        if isinstance(value, _Dfv):
            if value.is_trivial():
                key_parent = value.parents[0]
                assert isinstance(key_parent, _PKey)
                env.bind(name, _BDf(key_parent.ident_key))
                return
            ident = self._materialize(
                value, f"{name}@{node.lineno}", self._span(node)
            )
            env.bind(name, _BDf(ident.logical_key()))
        elif isinstance(value, _Session):
            env.bind(name, _BSession())
        elif isinstance(value, (_Writer, _WriterV2)):
            env.bind(name, _BWriter(value))
        elif isinstance(value, _Str):
            env.bind(name, _BStr(value.value))
        elif isinstance(value, _NotDf):
            env.bind(name, _BNotDf())
        else:
            env.bind(name, _BUnknown())

    # -- functions -------------------------------------------------------------

    def _function(
        self, st: ast.FunctionDef | ast.AsyncFunctionDef, env: _Env
    ) -> None:
        for d in st.decorator_list:
            self._after_eval(self._eval(d, env), d)
        args = st.args
        for d in args.defaults:
            self._after_eval(self._eval(d, env), d)
        for kd in args.kw_defaults:
            if kd is not None:
                self._after_eval(self._eval(kd, env), kd)
        env.push()
        params: list[ast.arg] = [
            *args.posonlyargs, *args.args, *args.kwonlyargs,
        ]
        if args.vararg is not None:
            params.append(args.vararg)
        if args.kwarg is not None:
            params.append(args.kwarg)
        for p in params:
            ann = _ann_name(p.annotation)
            if p.arg in _SESSION_NAMES or ann == "SparkSession":
                env.bind(p.arg, _BSession())
            elif ann == "DataFrame":
                env.bind(p.arg, _BParamDf(p.arg))
        self._stmts(st.body, env)
        env.pop()

    # -- materialização / emissão --------------------------------------------

    def _flatten(self, dfv: _Dfv, out: _Flat) -> None:
        out.reads.extend(dfv.reads)
        out.sql.extend(dfv.sql)
        for p in dfv.parents:
            if isinstance(p, _PKey):
                if p.ident_key not in out.pkeys:
                    out.pkeys.append(p.ident_key)
            elif isinstance(p, _PAnon):
                self._flatten(p.dfv, out)
            else:
                out.unres.append((p.text, p.span))

    def _ident_by_key(self, key: str) -> DomainIdentity:
        for acc in self.sink.entities.values():
            if acc.entity.identity.logical_key() == key:
                return acc.entity.identity
        raise KeyError(key)

    def _expand_view_sources(self, keys: set[str]) -> set[str]:
        """Expande temp views registradas para as fontes terminais."""
        out: set[str] = set()
        for k in keys:
            vs = self.sink.view_sources.get(k)
            out.update(vs if vs else {k})
        return out

    def _materialize(
        self, dfv: _Dfv, anchor: str, span: SourceSpan | None
    ) -> DomainIdentity:
        """Cria o ``data_transformation`` + arestas; devolve a identidade."""
        if dfv.is_trivial():
            pk = dfv.parents[0]
            assert isinstance(pk, _PKey)
            return self._ident_by_key(pk.ident_key)
        flat = _Flat()
        self._flatten(dfv, flat)
        ident = self.sink.transform_ident(self.path, anchor)
        key = ident.logical_key()
        evi = self.sink.evi(
            self.path, self.sha, span, notes=f"df:{anchor}"
        )
        operation = (
            "sql" if flat.sql
            else "read" if flat.reads and not flat.pkeys
            else "transform"
        )
        variable = "" if anchor.startswith("expr@") else anchor.split(
            "@", 1
        )[0]
        attrs = {
            "artifact": self.path,
            "variable": variable,
            "operation": operation,
            "line": str(anchor.rsplit("@", 1)[-1]),
        }
        self.sink.add_entity(
            ident, DomainEntityKind.DATA_TRANSFORMATION, anchor,
            path=self.path, span=span, content_hash=self.sha,
            attributes=attrs, evidence=(evi,),
        )
        src_keys: set[str] = set()
        for r in flat.reads:
            src_keys.add(r.ident_key)
            self.sink.add_rel(
                DomainRelationKind.READS_FROM.value, ident,
                path=self.path, to_i=self._ident_by_key(r.ident_key),
                span=r.span, evidence=(evi,),
            )
        for pkey in flat.pkeys:
            if pkey == key:
                continue  # auto-aresta não é linhagem
            self.sink.add_rel(
                DomainRelationKind.DERIVES_FROM.value, ident,
                path=self.path,
                to_i=self._ident_by_key(pkey),
                evidence=(evi,),
            )
            src_keys.update(self.sink.sources.get(pkey, set()))
        partial = False
        for text, pspan in flat.unres:
            partial = True
            self.sink.add_rel(
                DomainRelationKind.DERIVES_FROM.value, ident,
                path=self.path, to_text=text,
                span=pspan or span, evidence=(evi,),
            )
        for lit in flat.sql:
            sql_src, sql_unres = delegate_sql_literal(
                self.sink, lit, self.path, self.sha,
                call_evi=evi, call_span=span, t_ident=ident,
            )
            src_keys.update(sql_src)
            partial = partial or sql_unres
        if partial:
            self.sink.set_entity_attr(key, "lineage", "partial")
        self.sink.sources[key] = self._expand_view_sources(src_keys)
        self.transform_idents.append(ident)
        self.sink.bump("transformations")
        return ident

    # -- avaliação de expressões ----------------------------------------------

    def _eval(self, node: ast.expr, env: _Env) -> _E:
        handler = _EVAL_DISPATCH.get(type(node))
        if handler is not None:
            return handler(self, node, env)
        # ``"a" + "b"`` e afins foldam em literal — propagação honesta
        lit = _literal_str(node)
        if lit is not None:
            return _Str(lit)
        # BinOp/Compare/BoolOp/UnaryOp/Lambda/Slice/FormattedValue e
        # demais — avalia filhos para efeitos colaterais.
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                self._after_eval(self._eval(child, env), child)
        return _Unknown(self._txt(node), self._span(node))

    # -- handlers de expressão (``_EVAL_DISPATCH``) ----------------------------

    def _e_name(self, node: ast.Name, env: _Env) -> _E:
        b = env.lookup(node.id)
        if isinstance(b, _BSession):
            return _Session()
        if isinstance(b, _BDf):
            return _Dfv(parents=[_PKey(b.ident_key)])
        if isinstance(b, _BWriter):
            return b.writer
        if isinstance(b, _BParamDf):
            return _Dfv(
                parents=[_PUnres(f"param:{node.id}", self._span(node))]
            )
        if isinstance(b, _BStr):
            return _Str(b.value)
        if isinstance(b, _BNotDf):
            return _NotDf()
        if node.id in self.spark_classes:
            return _SClass()
        return _Unknown(node.id, self._span(node))

    def _e_walrus(self, node: ast.NamedExpr, env: _Env) -> _E:
        v = self._eval(node.value, env)
        if isinstance(node.target, ast.Name):
            self._bind_name(node.target.id, v, env, node.target)
        return v

    def _e_ifexp(self, node: ast.IfExp, env: _Env) -> _E:
        self._eval(node.test, env)
        a = self._eval(node.body, env)
        b = self._eval(node.orelse, env)
        return self._merge_ifexp(a, b, node)

    def _e_seq(
        self, node: ast.Tuple | ast.List | ast.Set, env: _Env
    ) -> _E:
        return _SeqE([self._eval(el, env) for el in node.elts])

    def _e_dict(self, node: ast.Dict, env: _Env) -> _E:
        for v in node.values:
            self._after_eval(self._eval(v, env), v)
        for k in node.keys:
            if k is not None:
                self._after_eval(self._eval(k, env), k)
        return _NotDf()

    def _e_const(self, node: ast.Constant, env: _Env) -> _E:
        del env
        if isinstance(node.value, str):
            return _Str(node.value)
        return _NotDf()

    def _e_fstr(self, node: ast.JoinedStr, env: _Env) -> _E:
        for v in node.values:
            if isinstance(v, ast.FormattedValue):
                self._after_eval(self._eval(v.value, env), v.value)
        # f-string sem placeholders é literal estático
        lit = _literal_str(node)
        return _Str(lit) if lit is not None else _NotDf()

    def _e_sub(self, node: ast.Subscript, env: _Env) -> _E:
        self._after_eval(self._eval(node.value, env), node.value)
        self._after_eval(self._eval(node.slice, env), node.slice)
        return _Unknown(self._txt(node), self._span(node))

    def _e_unwrap(
        self, node: ast.Await | ast.Starred | ast.YieldFrom, env: _Env
    ) -> _E:
        # o valor interno é quem conta
        return self._eval(node.value, env)

    def _e_yield(self, node: ast.Yield, env: _Env) -> _E:
        if node.value is not None:
            return self._eval(node.value, env)
        return _NotDf()

    def _e_comp(
        self,
        node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
        env: _Env,
    ) -> _E:
        self._comprehension(node, env)
        return _NotDf()

    def _merge_ifexp(self, a: _E, b: _E, node: ast.IfExp) -> _E:
        da, db = isinstance(a, _Dfv), isinstance(b, _Dfv)
        if da and db:
            assert isinstance(a, _Dfv) and isinstance(b, _Dfv)
            return _Dfv(parents=[_PAnon(a), _PAnon(b)])
        if da:
            assert isinstance(a, _Dfv)
            return _Dfv(
                parents=[
                    _PAnon(a), _PUnres(self._txt(node.orelse), None)
                ]
            )
        if db:
            assert isinstance(b, _Dfv)
            return _Dfv(
                parents=[
                    _PAnon(b), _PUnres(self._txt(node.body), None)
                ]
            )
        return _Unknown(self._txt(node), self._span(node))

    def _comprehension(self, node: ast.expr, env: _Env) -> None:
        for g in node.generators:  # type: ignore[attr-defined]
            self._after_eval(self._eval(g.iter, env), g.iter)
            for cond in g.ifs:
                self._after_eval(self._eval(cond, env), cond)
        if isinstance(node, ast.DictComp):
            self._after_eval(self._eval(node.key, env), node.key)
            self._after_eval(self._eval(node.value, env), node.value)
        else:
            elt = getattr(node, "elt", None)
            if elt is not None:
                self._after_eval(self._eval(elt, env), elt)

    def _attribute(self, node: ast.Attribute, env: _Env) -> _E:
        # self.spark / cls.spark — sessão por convenção de atributo
        if (
            node.attr in _SESSION_ATTRS
            and isinstance(node.value, ast.Name)
            and node.value.id in ("self", "cls")
        ):
            return _Session()
        base = self._eval(node.value, env)
        if isinstance(base, _Session):
            if node.attr in _SESSION_READ_ATTRS:
                return _Reader()
            return _Unknown(self._txt(node), self._span(node))
        if isinstance(base, _SClass):
            return (
                _SBuilder()
                if node.attr == "builder"
                else _Unknown(self._txt(node), self._span(node))
            )
        if isinstance(base, _SBuilder):
            return base
        if isinstance(base, _Dfv):
            return self._dfv_attr(base, node)
        if isinstance(base, _Unknown):
            return self._unknown_attr(base, node)
        return _Unknown(self._txt(node), self._span(node))

    def _dfv_attr(self, base: _Dfv, node: ast.Attribute) -> _E:
        if node.attr in ("write", "writeStream"):
            return _Writer(dfv=base, span=self._span(node))
        if node.attr == "na":
            return _NaOps(base)
        if node.attr in _DF_NON_DF_ATTRS:
            return _NotDf()
        return _Unknown(self._txt(node), self._span(node))

    def _unknown_attr(self, base: _Unknown, node: ast.Attribute) -> _E:
        if node.attr in ("write", "writeStream"):
            return _Writer(
                dfv=None, unknown_recv=base.text,
                span=self._span(node),
            )
        return _Unknown(
            f"{base.text}.{node.attr}", self._span(node)
        )

    # -- calls ---------------------------------------------------------------

    def _call(self, node: ast.Call, env: _Env) -> _E:
        fn = node.func
        if isinstance(fn, ast.Attribute):
            recv = self._eval(fn.value, env)
            return self._method(recv, fn.attr, node, env)
        if isinstance(fn, ast.Name):
            if fn.id in self.spark_classes:
                return _SClass()
            # função transparente: broadcast(df) → df
            if fn.id in _TRANSPARENT_FNS and node.args:
                return self._eval(node.args[0], env)
            self._sweep_args(node, env)
            return _Unknown(self._txt(fn), self._span(fn))
        # callee exótico — ainda assim varre os argumentos
        self._sweep_args(node, env)
        return _Unknown(self._txt(node), self._span(node))

    def _sweep_args(self, node: ast.Call, env: _Env) -> None:
        """Avalia args de call desconhecido — efeitos colaterais contam."""
        for a in node.args:
            self._after_eval(self._eval(a, env), a)
        for kw in node.keywords:
            self._after_eval(self._eval(kw.value, env), kw.value)

    def _method(
        self, recv: _E, method: str, node: ast.Call, env: _Env
    ) -> _E:
        handler = _METHOD_RECV.get(type(recv))
        if handler is not None:
            return handler(self, recv, method, node, env)
        # _NotDf/_Str/_SeqE — chamada sobre não-DF: nada a propagar
        self._sweep_args(node, env)
        return _Unknown(self._txt(node.func), self._span(node))

    def _m_sclass(
        self, recv: _SClass, method: str, node: ast.Call, env: _Env
    ) -> _E:
        del recv
        if method == "builder":
            return _SBuilder()
        if method in ("getActiveSession", "active", "newSession"):
            return _Session()
        self._sweep_args(node, env)
        return _Unknown(self._txt(node.func), self._span(node))

    def _m_sbuilder(
        self, recv: _SBuilder, method: str, node: ast.Call, env: _Env
    ) -> _E:
        if method == "getOrCreate":
            return _Session()
        if method == "appName" and node.args:
            lit = self._literal_arg(node.args[0], env)
            if lit:
                self.app_name = lit.strip()
        self._sweep_args(node, env)
        return recv

    def _m_naops(
        self, recv: _NaOps, method: str, node: ast.Call, env: _Env
    ) -> _E:
        self._sweep_args(node, env)
        if method in _NA_METHODS:
            return recv.dfv
        return _Unknown(self._txt(node), self._span(node))

    # -- métodos de sessão ------------------------------------------------------

    def _session_method(
        self, recv: _Session, method: str, node: ast.Call, env: _Env
    ) -> _E:
        del recv
        if method in _SESSION_READ_ATTRS:
            self._sweep_args(node, env)
            return _Reader()
        if method == "sql":
            return self._spark_sql(node, env)
        if method == "table":
            return self._read_table(node, env, via="spark.table")
        if method in ("createDataFrame", "range"):
            self._sweep_args(node, env)
            return _Dfv()  # dados literais/locais — sem upstream
        if method in _SESSION_NON_DF:
            self._sweep_args(node, env)
            return _NotDf()
        self._sweep_args(node, env)
        return _Unknown(self._txt(node.func), self._span(node))

    def _spark_sql(self, node: ast.Call, env: _Env) -> _E:
        if not node.args:
            self.sink.diag(
                "empty-sql", "info",
                "spark.sql() without positional query", self.path,
            )
            self._sweep_args(node, env)
            return _Dfv()
        lit = self._literal_arg(node.args[0], env)
        span = self._span(node)
        if lit is None:
            self.sink.diag(
                "dynamic-sql", "warning",
                "spark.sql() argument is not a static string literal "
                "— no table deps fabricated", self.path,
            )
            self.sink.bump("sql_dynamic")
            self._sweep_args(node, env)
            return _Dfv(
                parents=[
                    _PUnres(f"sql:{self._txt(node.args[0])}", span)
                ]
            )
        self.sink.bump("sql_literals")
        self._sweep_args(node, env)
        return _Dfv(
            sql=[_SqlLit(literal=lit, span=span, line=node.lineno)]
        )

    def _read_table(self, node: ast.Call, env: _Env, *, via: str) -> _E:
        """``spark.table(t)`` / ``spark.read.table(t)`` — catálogo."""
        self._sweep_args(node, env)
        if not node.args:
            self.sink.diag(
                "missing-source", "info",
                f"{via}() without table argument", self.path,
            )
            return _Dfv()
        lit = self._literal_arg(node.args[0], env)
        span = self._span(node.args[0]) or self._span(node)
        if lit is None:
            self.sink.diag(
                "dynamic-source", "warning",
                f"{via}() table is not a static literal — "
                "unresolved read recorded", self.path,
            )
            return _Dfv(
                parents=[_PUnres(self._txt(node.args[0]), span)]
            )
        parts = split_table_name(lit)
        if parts is None:
            self.sink.diag(
                "dynamic-source", "warning",
                f"{via}() table name is dynamic: {lit!r}", self.path,
            )
            return _Dfv(
                parents=[_PUnres(f"table:{lit}", span)]
            )
        # temp view registrada neste arquivo sombreia o catálogo —
        # mantém a aresta de leitura na view (que deriva das fontes)
        tv_key = self.temp_views.get(".".join(parts))
        if tv_key is not None:
            return _Dfv(
                reads=[_Read(ident_key=tv_key, fmt="tempview", span=span)]
            )
        ident = self.sink.table_ident(parts)
        self._emit_table(
            ident, parts[-1], via=via, roles=("read",),
            written=lit, span=span,
        )
        return _Dfv(
            reads=[_Read(ident_key=ident.logical_key(), fmt="table",
                         span=span)]
        )

    # -- métodos de DataFrameReader ------------------------------------------------

    def _reader_method(
        self, recv: _Reader, method: str, node: ast.Call, env: _Env
    ) -> _E:
        if method == "format":
            fmt = self._literal_arg(node.args[0], env) if node.args else None
            self._sweep_args(node, env)
            return _Reader(fmt=fmt or recv.fmt)
        if method in ("schema", "option", "options"):
            self._sweep_args(node, env)
            return recv
        if method == "table":
            return self._read_table(node, env, via="spark.read.table")
        if method in _READER_PATH_FORMATS or method == "load":
            return self._read_path(node, env, recv, method)
        if method == "jdbc":
            return self._read_jdbc(node, env)
        self._sweep_args(node, env)
        return _Unknown(self._txt(node.func), self._span(node))

    def _read_path(
        self, node: ast.Call, env: _Env, recv: _Reader, method: str
    ) -> _E:
        """``spark.read.parquet|csv|json|…(path)`` e ``.format(f).load(path)``."""
        self._sweep_args(node, env)
        fmt = recv.fmt if method == "load" else method
        if not node.args:
            self.sink.diag(
                "missing-source", "info",
                f"spark.read.{method}() without path — source "
                "recorded as unresolved", self.path,
            )
            return _Dfv(
                parents=[_PUnres(f"read:{method}()", self._span(node))]
            )
        lit = self._literal_arg(node.args[0], env)
        span = self._span(node.args[0]) or self._span(node)
        if lit is None:
            self.sink.diag(
                "dynamic-source", "warning",
                f"spark.read.{method}() path is not a static "
                "literal — unresolved read recorded", self.path,
            )
            return _Dfv(
                parents=[_PUnres(self._txt(node.args[0]), span)]
            )
        ident = self.sink.path_ident(lit)
        self.sink.add_entity(
            ident, DomainEntityKind.DATA_TABLE, lit.strip(),
            path=self.path, span=span, content_hash=self.sha,
            attributes={
                "storage": "path", "format": fmt,
                "uri_scheme": uri_scheme(lit),
                "written_name": lit.strip(), "via": f"spark.read.{method}",
            },
            roles=("read",),
            evidence=(self.sink.evi(
                self.path, self.sha, span, notes=f"read:{method}"
            ),),
        )
        return _Dfv(
            reads=[_Read(ident_key=ident.logical_key(), fmt=fmt, span=span)]
        )

    def _read_jdbc(self, node: ast.Call, env: _Env) -> _E:
        """``spark.read.jdbc(url, table)`` — o catálogo é o 2º argumento."""
        self._sweep_args(node, env)
        if len(node.args) < 2:
            self.sink.diag(
                "missing-source", "info",
                "spark.read.jdbc() without table argument", self.path,
            )
            return _Dfv()
        lit = self._literal_arg(node.args[1], env)
        span = self._span(node.args[1]) or self._span(node)
        if lit is None:
            self.sink.diag(
                "dynamic-source", "warning",
                "spark.read.jdbc() table is not a static literal",
                self.path,
            )
            return _Dfv(
                parents=[_PUnres(self._txt(node.args[1]), span)]
            )
        parts = split_table_name(lit)
        if parts is None:
            return _Dfv(parents=[_PUnres(f"table:{lit}", span)])
        ident = self.sink.table_ident(parts)
        self._emit_table(
            ident, parts[-1], via="spark.read.jdbc", roles=("read",),
            written=lit, span=span,
        )
        return _Dfv(
            reads=[_Read(ident_key=ident.logical_key(), fmt="jdbc",
                         span=span)]
        )

    # -- métodos de DataFrame ---------------------------------------------------

    def _df_method(
        self, recv: _Dfv, method: str, node: ast.Call, env: _Env
    ) -> _E:
        if method == "writeTo":
            return self._write_to(recv, node, env)
        if method in _TEMP_VIEW_METHODS:
            return self._temp_view(recv, method, node, env)
        if method in _DF_TRANSFORMS:
            parents: list[_Parent] = [_PAnon(recv)]
            for i, a in enumerate(node.args):
                e = self._eval(a, env)
                if i == 0 and method in _DF_ARG_OPS:
                    if isinstance(e, _Dfv):
                        parents.append(_PAnon(e))
                    elif isinstance(e, _Unknown):
                        parents.append(_PUnres(e.text, e.span))
                    else:
                        self._after_eval(e, a)
                else:
                    self._after_eval(e, a)
            for kw in node.keywords:
                self._after_eval(self._eval(kw.value, env), kw.value)
            return _Dfv(parents=parents)
        if method in _DF_NON_DF_CALLS:
            self._sweep_args(node, env)
            return _NotDf()
        # método desconhecido sobre DF conhecido — honesto: não sabemos
        self._sweep_args(node, env)
        return _Unknown(self._txt(node.func), self._span(node))

    def _temp_view(
        self, recv: _Dfv, method: str, node: ast.Call, env: _Env
    ) -> _E:
        self._sweep_args(node, env)
        if not node.args:
            return _NotDf()
        lit = self._literal_arg(node.args[0], env)
        if lit is None:
            self.sink.diag(
                "dynamic-source", "warning",
                f"df.{method}() view name is not a static literal",
                self.path,
            )
            return _NotDf()
        name = lit.strip()
        if not name:
            return _NotDf()
        global_view = "Global" in method
        parts = ("global_temp", name) if global_view else (name,)
        flat = _Flat()
        self._flatten(recv, flat)
        ident = self.sink.table_ident(parts)
        evi = self.sink.evi(
            self.path, self.sha, self._span(node), notes=f"tempview:{name}"
        )
        self.sink.add_entity(
            ident, DomainEntityKind.DATA_VIEW, name,
            path=self.path, span=self._span(node),
            content_hash=self.sha,
            attributes={
                "temporary": "true", "via": f"df.{method}",
                "written_name": name,
            },
            roles=("declare",), evidence=(evi,),
        )
        self.temp_views[".".join(parts)] = ident.logical_key()
        vsrc: set[str] = set()
        for r in flat.reads:
            vsrc.add(r.ident_key)
            self.sink.add_rel(
                DomainRelationKind.DERIVES_FROM.value, ident,
                path=self.path, to_i=self._ident_by_key(r.ident_key),
                evidence=(evi,),
            )
        for pk in flat.pkeys:
            if pk == ident.logical_key():
                continue
            self.sink.add_rel(
                DomainRelationKind.DERIVES_FROM.value, ident,
                path=self.path, to_i=self._ident_by_key(pk),
                evidence=(evi,),
            )
            vsrc.update(self.sink.sources.get(pk, set()))
        self.sink.view_sources[ident.logical_key()] = vsrc
        self.sink.bump("temp_views")
        return _NotDf()

    # -- writes -----------------------------------------------------------------

    def _writer_method(
        self, recv: _Writer, method: str, node: ast.Call, env: _Env
    ) -> _E:
        if method == "mode":
            lit = (
                self._literal_arg(node.args[0], env)
                if node.args else None
            )
            self._sweep_args(node, env)
            return _Writer(
                dfv=recv.dfv, unknown_recv=recv.unknown_recv,
                mode=lit or recv.mode, fmt=recv.fmt, span=recv.span,
            )
        if method == "format":
            lit = (
                self._literal_arg(node.args[0], env)
                if node.args else None
            )
            self._sweep_args(node, env)
            return _Writer(
                dfv=recv.dfv, unknown_recv=recv.unknown_recv,
                mode=recv.mode, fmt=lit or recv.fmt, span=recv.span,
            )
        if method in _WRITER_OPTS:
            self._sweep_args(node, env)
            return recv
        if method in _WRITER_PATH_TERMINALS:
            self._emit_path_write(recv, node, env, method)
            return _NotDf()
        if method in _WRITER_TABLE_TERMINALS:
            self._emit_table_write(recv, node, env, method)
            return _NotDf()
        self._sweep_args(node, env)
        return _Unknown(self._txt(node.func), self._span(node))

    def _v2_method(
        self, recv: _WriterV2, method: str, node: ast.Call, env: _Env
    ) -> _E:
        if method in _V2_OPTS:
            via = recv.via
            if method == "using" and node.args:
                lit = self._literal_arg(node.args[0], env)
                if lit:
                    via = lit
            self._sweep_args(node, env)
            return _WriterV2(
                dfv=recv.dfv, unknown_recv=recv.unknown_recv,
                target_lit=recv.target_lit,
                target_span=recv.target_span, via=via, span=recv.span,
            )
        if method in _V2_TERMINALS:
            self._emit_v2_write(recv, node, env, method)
            return _NotDf()
        self._sweep_args(node, env)
        return _Unknown(self._txt(node.func), self._span(node))

    def _write_to(self, recv: _Dfv, node: ast.Call, env: _Env) -> _E:
        """``df.writeTo("cat.t")`` — alvo é o 1º argumento."""
        target_lit: str | None = None
        target_span: SourceSpan | None = None
        if node.args:
            target_lit = self._literal_arg(node.args[0], env)
            target_span = self._span(node.args[0])
        for a in node.args[1:]:
            self._after_eval(self._eval(a, env), a)
        for kw in node.keywords:
            self._after_eval(self._eval(kw.value, env), kw.value)
        return _WriterV2(
            dfv=recv, target_lit=target_lit, target_span=target_span,
            span=self._span(node),
        )

    def _emit_write_base(
        self, dfv: _Dfv | None, unknown_recv: str, node: ast.Call
    ) -> tuple[DomainIdentity, set[str]]:
        """Materializa o receiver do write → (identidade T, fontes)."""
        span = self._span(node)
        if dfv is not None:
            ident = self._materialize(dfv, f"expr@{node.lineno}", span)
            return ident, self.sink.sources.get(ident.logical_key(), set())
        self.sink.diag(
            "unresolved-dataframe", "info",
            f"write on unresolved receiver {unknown_recv!r} — "
            "partial lineage", self.path,
        )
        ident = self.sink.transform_ident(
            self.path, f"expr@{node.lineno}"
        )
        evi = self.sink.evi(
            self.path, self.sha, span, notes="df:write"
        )
        self.sink.add_entity(
            ident, DomainEntityKind.DATA_TRANSFORMATION,
            f"expr@{node.lineno}", path=self.path, span=span,
            content_hash=self.sha,
            attributes={
                "artifact": self.path, "operation": "write",
                "line": str(node.lineno), "lineage": "partial",
            },
            evidence=(evi,),
        )
        self.sink.add_rel(
            DomainRelationKind.DERIVES_FROM.value, ident,
            path=self.path, to_text=unknown_recv,
            span=span, evidence=(evi,),
        )
        self.transform_idents.append(ident)
        return ident, set()

    def _emit_path_write(
        self, recv: _Writer, node: ast.Call, env: _Env, method: str
    ) -> None:
        for a in node.args[1:]:
            self._after_eval(self._eval(a, env), a)
        for kw in node.keywords:
            self._after_eval(self._eval(kw.value, env), kw.value)
        tid, srcs = self._emit_write_base(recv.dfv, recv.unknown_recv, node)
        span = self._span(node)
        evi = self.sink.evi(self.path, self.sha, span, notes="df:write")
        if not node.args:
            self.sink.diag(
                "missing-target", "info",
                f"df.write.{method}() without target", self.path,
            )
            return
        lit = self._literal_arg(node.args[0], env)
        if lit is None:
            self.sink.diag(
                "dynamic-write-target", "warning",
                f"df.write.{method}() target is not a static literal",
                self.path,
            )
            self.sink.add_rel(
                DomainRelationKind.WRITES_TO.value, tid,
                path=self.path, to_text=self._txt(node.args[0]),
                span=span, evidence=(evi,),
            )
            return
        fmt = method if method != "save" else (recv.fmt or "save")
        ident = self.sink.path_ident(lit)
        arg_span = self._span(node.args[0]) or span
        self.sink.add_entity(
            ident, DomainEntityKind.DATA_TABLE, lit.strip(),
            path=self.path, span=arg_span, content_hash=self.sha,
            attributes={
                "storage": "path", "format": fmt,
                "uri_scheme": uri_scheme(lit),
                "written_name": lit.strip(), "via": f"df.write.{method}",
            },
            roles=("write",),
            evidence=(self.sink.evi(
                self.path, self.sha, arg_span, notes="df:write.target"
            ),),
        )
        self._write_edges(tid, ident, srcs, span, evi)

    def _emit_table_write(
        self, recv: _Writer, node: ast.Call, env: _Env, method: str
    ) -> None:
        # jdbc(url, table) usa o 2º argumento; demais usam o 1º
        arg_idx = 1 if method == "jdbc" else 0
        for i, a in enumerate(node.args):
            if i != arg_idx:
                self._after_eval(self._eval(a, env), a)
        for kw in node.keywords:
            self._after_eval(self._eval(kw.value, env), kw.value)
        tid, srcs = self._emit_write_base(recv.dfv, recv.unknown_recv, node)
        span = self._span(node)
        evi = self.sink.evi(self.path, self.sha, span, notes="df:write")
        if len(node.args) <= arg_idx:
            self.sink.diag(
                "missing-target", "info",
                f"df.write.{method}() without target", self.path,
            )
            return
        lit = self._literal_arg(node.args[arg_idx], env)
        if lit is None:
            self.sink.diag(
                "dynamic-write-target", "warning",
                f"df.write.{method}() target is not a static literal",
                self.path,
            )
            self.sink.add_rel(
                DomainRelationKind.WRITES_TO.value, tid,
                path=self.path,
                to_text=self._txt(node.args[arg_idx]),
                span=span, evidence=(evi,),
            )
            return
        parts = split_table_name(lit)
        if parts is None:
            self.sink.add_rel(
                DomainRelationKind.WRITES_TO.value, tid,
                path=self.path, to_text=f"table:{lit}",
                span=span, evidence=(evi,),
            )
            return
        ident = self.sink.table_ident(parts)
        arg_span = self._span(node.args[arg_idx]) or span
        self._emit_table(
            ident, parts[-1], via=f"df.write.{method}", roles=("write",),
            mode=recv.mode, written=lit, span=arg_span,
        )
        self._write_edges(tid, ident, srcs, span, evi)

    def _emit_v2_write(
        self, recv: _WriterV2, node: ast.Call, env: _Env, method: str
    ) -> None:
        self._sweep_args(node, env)
        tid, srcs = self._emit_write_base(recv.dfv, recv.unknown_recv, node)
        span = self._span(node)
        evi = self.sink.evi(self.path, self.sha, span, notes="df:writeTo")
        lit = recv.target_lit
        if lit is None:
            self.sink.diag(
                "dynamic-write-target", "warning",
                f"df.writeTo().{method}() target is not a static "
                "literal", self.path,
            )
            self.sink.add_rel(
                DomainRelationKind.WRITES_TO.value, tid,
                path=self.path, to_text="writeTo(<dynamic>)",
                span=span, evidence=(evi,),
            )
            return
        parts = split_table_name(lit)
        if parts is None:
            self.sink.add_rel(
                DomainRelationKind.WRITES_TO.value, tid,
                path=self.path, to_text=f"table:{lit}",
                span=span, evidence=(evi,),
            )
            return
        ident = self.sink.table_ident(parts)
        tgt_span = recv.target_span or span
        self.sink.add_entity(
            ident, DomainEntityKind.ICEBERG_TABLE, parts[-1],
            path=self.path, span=tgt_span,
            content_hash=self.sha,
            attributes={
                "api": "df.writeTo", "written_name": lit.strip(),
                "using": recv.via, "op": method,
            },
            roles=("write",),
            evidence=(self.sink.evi(
                self.path, self.sha, tgt_span, notes="df:writeTo.target"
            ),),
        )
        self._write_edges(tid, ident, srcs, span, evi)

    def _write_edges(
        self,
        tid: DomainIdentity,
        sink_ident: DomainIdentity,
        srcs: set[str],
        span: SourceSpan | None,
        evi: int,
    ) -> None:
        self.sink.add_rel(
            DomainRelationKind.WRITES_TO.value, tid,
            path=self.path, to_i=sink_ident,
            span=span, evidence=(evi,),
        )
        skey = sink_ident.logical_key()
        for sk in sorted(srcs):
            if sk == skey:
                continue  # auto-derivação não é aresta
            self.sink.add_rel(
                DomainRelationKind.DERIVES_FROM.value, sink_ident,
                path=self.path, to_i=self._ident_by_key(sk),
                span=span, evidence=(evi,),
            )
        self.sink.bump("writes")

    # -- desconhecido ------------------------------------------------------------

    def _unknown_method(
        self, recv: _Unknown, method: str, node: ast.Call, env: _Env
    ) -> _E:
        if method == "writeTo":
            target_lit = (
                self._literal_arg(node.args[0], env)
                if node.args else None
            )
            for a in node.args[1:]:
                self._after_eval(self._eval(a, env), a)
            for kw in node.keywords:
                self._after_eval(self._eval(kw.value, env), kw.value)
            return _WriterV2(
                dfv=None, unknown_recv=recv.text,
                target_lit=target_lit,
                target_span=(
                    self._span(node.args[0]) if node.args else None
                ),
                span=self._span(node),
            )
        if method in _DF_TRANSFORMS:
            self.sink.diag(
                "unresolved-dataframe", "info",
                f"DataFrame op {method!r} on unresolved receiver "
                f"{recv.text!r} — partial lineage", self.path,
            )
            parents: list[_Parent] = [_PUnres(recv.text, recv.span)]
            for i, a in enumerate(node.args):
                e = self._eval(a, env)
                if i == 0 and method in _DF_ARG_OPS:
                    if isinstance(e, _Dfv):
                        parents.append(_PAnon(e))
                    elif isinstance(e, _Unknown):
                        parents.append(_PUnres(e.text, e.span))
                    else:
                        self._after_eval(e, a)
                else:
                    self._after_eval(e, a)
            for kw in node.keywords:
                self._after_eval(self._eval(kw.value, env), kw.value)
            return _Dfv(parents=parents)
        self._sweep_args(node, env)
        return _Unknown(
            f"{recv.text}.{method}", self._span(node)
        )

    # -- pós-avaliação --------------------------------------------------------------

    def _after_eval(self, value: _E, node: ast.AST | None) -> None:
        """Materializa valores-DF "interessantes" achados soltos."""
        if isinstance(value, _Dfv):
            if value.is_interesting():
                line = getattr(node, "lineno", 0) if node is not None else 0
                self._materialize(
                    value, f"expr@{line}",
                    self._span(node) if node is not None else None,
                )
        elif isinstance(value, (_Writer, _WriterV2)):
            self.sink.diag(
                "write-never-executed", "info",
                "DataFrameWriter built but no terminal op observed "
                "— no write emitted", self.path,
            )

    # -- emissão compartilhada -------------------------------------------------

    def _emit_table(
        self, ident: DomainIdentity, name: str, *, via: str,
        roles: tuple[str, ...], written: str,
        span: SourceSpan | None,
        mode: str = "",
    ) -> None:
        attrs = {
            "storage": "catalog", "via": via, "written_name": written,
        }
        if mode:
            attrs["write_mode"] = mode
        self.sink.add_entity(
            ident, DomainEntityKind.DATA_TABLE, name,
            path=self.path, span=span, content_hash=self.sha,
            attributes=attrs, roles=roles,
            evidence=(self.sink.evi(self.path, self.sha, span,
                                    notes=f"table:{name}"),),
        )


# -- tabelas de dispatch -----------------------------------------------------
# ``Any`` no parâmetro do nó (mesmo desenho de ``sql.extract._STMT_HANDLERS``):
# a assinatura uniforme permite narrowings diferentes por handler.

_STMT_DISPATCH: dict[type[ast.stmt], Callable[[_Walker, Any, _Env], None]] = {
    ast.FunctionDef: _Walker._function,
    ast.AsyncFunctionDef: _Walker._function,
    ast.ClassDef: _Walker._st_class,
    ast.If: _Walker._st_if,
    ast.For: _Walker._st_for,
    ast.AsyncFor: _Walker._st_for,
    ast.While: _Walker._st_while,
    ast.With: _Walker._st_with,
    ast.AsyncWith: _Walker._st_with,
    ast.Try: _Walker._st_try,
    ast.TryStar: _Walker._st_try,
    ast.Return: _Walker._st_return,
    ast.Raise: _Walker._st_raise,
    ast.Delete: _Walker._st_delete,
    ast.Match: _Walker._st_match,
    ast.Assert: _Walker._st_assert,
}

_EVAL_DISPATCH: dict[type[ast.expr], Callable[[_Walker, Any, _Env], _E]] = {
    ast.Call: _Walker._call,
    ast.Name: _Walker._e_name,
    ast.Attribute: _Walker._attribute,
    ast.NamedExpr: _Walker._e_walrus,
    ast.IfExp: _Walker._e_ifexp,
    ast.Tuple: _Walker._e_seq,
    ast.List: _Walker._e_seq,
    ast.Set: _Walker._e_seq,
    ast.Dict: _Walker._e_dict,
    ast.Constant: _Walker._e_const,
    ast.JoinedStr: _Walker._e_fstr,
    ast.Subscript: _Walker._e_sub,
    ast.Await: _Walker._e_unwrap,
    ast.Starred: _Walker._e_unwrap,
    ast.YieldFrom: _Walker._e_unwrap,
    ast.Yield: _Walker._e_yield,
    ast.ListComp: _Walker._e_comp,
    ast.SetComp: _Walker._e_comp,
    ast.DictComp: _Walker._e_comp,
    ast.GeneratorExp: _Walker._e_comp,
}

_METHOD_RECV: dict[type[_E], Callable[[_Walker, Any, str, ast.Call, _Env], _E]] = {
    _Session: _Walker._session_method,
    _SClass: _Walker._m_sclass,
    _SBuilder: _Walker._m_sbuilder,
    _Reader: _Walker._reader_method,
    _Dfv: _Walker._df_method,
    _NaOps: _Walker._m_naops,
    _Writer: _Walker._writer_method,
    _WriterV2: _Walker._v2_method,
    _Unknown: _Walker._unknown_method,
}
