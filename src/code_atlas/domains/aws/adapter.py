"""``AwsDataAdapter`` — especialização AWS-data (lf-0113, Cycle 7 Ph.5).

Adapter **opcional** de inteligência de domínio: inspeciona estaticamente
artefatos AWS-orientados do repositório — nunca executa jobs, nunca abre
conexão AWS, nunca roda ``boto3``/``aws``/Spark/Glue/EMR. Toda extração é
evidence-first: fatos carregam ``EvidenceRecord`` sobre conteúdo real e
estados honestos de resolução (``unresolved``/``external`` quando o
texto não prova o alvo).

Artefatos reconhecidos (todos opcionais — nenhum é obrigatório):

- scripts ``.py`` de job Glue/Spark (``awsglue``/``GlueContext``/
  ``DynamicFrame``/``from_catalog``/``from_options``/``getDataFrame``/
  ``write_dynamic_frame.from_catalog``/``job.init``/``job.commit``/
  bookmarks/``getResolvedOptions``) — AST stdlib, zero execução;
- literais ``spark.sql(...)``/``execute(...)`` embutidos — SQL parseado
  via **reuso do adapter ``domains.sql``** (``parse_file`` +
  ``extract_parsed``), identidades re-marcadas ``sql`` → ``aws-data``;
- ``writeTo``/``insertInto``/``saveAsTable``/meta-tabelas Iceberg
  (``cat.db.t.snapshots`` …) → ``iceberg_table`` quando o texto prova
  Iceberg (``.using("iceberg")``, ``format("iceberg")`` ou catálogo
  declarado como implementação iceberg);
- ``.sql`` (Athena/Spark/ANSI) — mesmo reuso do parser sql;
- ``.json``/``.yaml``: definições de job Glue (``Name``/``Role``/
  ``Command``/``DefaultArguments``), steps EMR (``Steps``/
  ``HadoopJarStep``/``command-runner.jar spark-submit``), config de
  cluster EMR (``JobFlowRole``…), triggers Glue
  (``Type``/``Actions``/``Predicate``);
- ``.sh`` com linhas ``spark-submit`` (``--conf``, script, args) e
  ``.conf``/``.properties`` com chaves ``spark.sql.catalog.*``;
- literais ``s3://`` e ARNs IAM → ``data_table`` S3 / ``iam_role`` +
  ``READS_FROM``/``WRITES_TO``/``REFERENCES``/``ASSUMES_ROLE``.

Identidade AWS explícita: conta/região/catálogo só entram nos segmentos
quando escritos no artefato (ARN ``catalog`` = ``aws:<svc>:<acct>[:<rgn>]``,
``catalog_id`` Glue = ``aws:glue:<acct>``); valores ausentes permanecem
vazios — entidades homônimas em contas/catálogos distintos NÃO colidem.
"""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath

from code_atlas.contracts.enums import ConfidenceClass
from code_atlas.domains.aws import configs, pyfiles
from code_atlas.domains.aws.emit import ADAPTER_DOMAIN, Sink
from code_atlas.domains.aws.sqlbridge import extract_sql_fragment
from code_atlas.domains.contracts import (
    ArtifactSupport,
    ConfidenceModel,
    DiscoveryRule,
    DomainDiagnostic,
    DomainExtractionRequest,
    DomainExtractionResult,
)
from code_atlas.domains.model import DomainEntityKind, DomainRelationKind
from code_atlas.domains.sql.adapter import looks_like_sql

DOMAIN_ID = ADAPTER_DOMAIN  # "aws-data"
ADAPTER_VERSION = "0.1.0"

_MAX_ARTIFACT = 4 << 20  # 4 MiB — mesmo guarda-rail dos adapters api/sql

_SUPPORT = ArtifactSupport(
    extensions=frozenset({".py", ".sql", ".json", ".yaml", ".yml", ".sh", ".conf", ".properties"}),
    globs=("*.sql.j2", "*.hql"),
)

_ENTITY_KINDS = frozenset(
    {
        DomainEntityKind.DATA_TABLE,
        DomainEntityKind.DATA_COLUMN,
        DomainEntityKind.DATA_VIEW,
        DomainEntityKind.DATA_TRANSFORMATION,
        DomainEntityKind.DATA_PIPELINE,
        DomainEntityKind.SPARK_JOB,
        DomainEntityKind.GLUE_JOB,
        DomainEntityKind.DATA_CATALOG,
        DomainEntityKind.ICEBERG_TABLE,
        DomainEntityKind.IAM_ROLE,
    }
)

_RELATION_KINDS = frozenset(
    {
        str(DomainRelationKind.READS_FROM),
        str(DomainRelationKind.WRITES_TO),
        str(DomainRelationKind.TRANSFORMS),
        str(DomainRelationKind.DERIVES_FROM),
        str(DomainRelationKind.DEPENDS_ON),
        str(DomainRelationKind.ASSUMES_ROLE),
        str(DomainRelationKind.TRIGGERS),
        str(DomainRelationKind.DEPLOYS),
        str(DomainRelationKind.REFERENCES),
        "CONTAINS",  # EdgeKind — módulo/step/script → fato contido
    }
)

_DISCOVERY_RULES: tuple[DiscoveryRule, ...] = (
    DiscoveryRule(
        glob="glue/**/*.py",
        artifact_kind="glue-job-script",
        notes=(
            "Glue ETL scripts — GlueContext/DynamicFrame markers "
            "content-sniffed; no execution, ever"
        ),
    ),
    DiscoveryRule(
        glob="**/glue/*.py",
        artifact_kind="glue-job-script",
        notes="nested glue dir convention",
    ),
    DiscoveryRule(
        glob="jobs/**/*.py",
        artifact_kind="job-script",
        notes="generic job dir — spark/glue markers decide",
    ),
    DiscoveryRule(
        glob="scripts/**/*.py",
        artifact_kind="job-script",
        notes="generic scripts dir — markers decide",
    ),
    DiscoveryRule(
        glob="*.py",
        artifact_kind="maybe-aws-script",
        notes=(
            "any Python — extracted only when awsglue/pyspark/boto3 "
            "markers or s3/arn literals are statically visible"
        ),
    ),
    DiscoveryRule(
        glob="athena/**/*.sql",
        artifact_kind="athena-sql",
        notes="Athena/Trino SQL — parsed via domains.sql reuse",
    ),
    DiscoveryRule(
        glob="**/athena/*.sql",
        artifact_kind="athena-sql",
        notes="nested athena dir convention",
    ),
    DiscoveryRule(
        glob="*.sql",
        artifact_kind="sql-script",
        notes=(
            "any SQL — Athena/Spark/ANSI sniffed by content; non-SQL content becomes a diagnostic"
        ),
    ),
    DiscoveryRule(
        glob="emr/**/*.json",
        artifact_kind="emr-config",
        notes="EMR steps/cluster config conventions",
    ),
    DiscoveryRule(
        glob="emr/**/*.yaml",
        artifact_kind="emr-config",
        notes="EMR steps/cluster config (yaml subset)",
    ),
    DiscoveryRule(
        glob="*steps*.json",
        artifact_kind="emr-step-config",
        notes="EMR add-steps payload shape sniffed by keys",
    ),
    DiscoveryRule(
        glob="*steps*.yaml",
        artifact_kind="emr-step-config",
        notes="EMR add-steps payload (yaml subset)",
    ),
    DiscoveryRule(
        glob="*glue*job*.json",
        artifact_kind="glue-job-definition",
        notes="Glue job definition JSON — Name/Role/Command shape",
    ),
    DiscoveryRule(
        glob="*trigger*.json",
        artifact_kind="glue-trigger",
        notes="Glue trigger/workflow action shape",
    ),
    DiscoveryRule(
        glob="*.json",
        artifact_kind="maybe-aws-config",
        notes=(
            "any JSON — only extracted when an AWS shape is proven "
            "(Name+Role+Command, Steps, Actions+Type, *Role keys)"
        ),
    ),
    DiscoveryRule(
        glob="*.sh",
        artifact_kind="shell-script",
        notes="spark-submit lines extracted; script never executed",
    ),
    DiscoveryRule(
        glob="*.conf",
        artifact_kind="spark-conf",
        notes="spark.* properties — spark.sql.catalog.* declarations",
    ),
    DiscoveryRule(
        glob="*.properties",
        artifact_kind="spark-conf",
        notes="spark-defaults style properties",
    ),
)


class AwsDataAdapter:
    """Adapter ``aws-data`` — especialização AWS estática e evidence-first.

    ``base_path``: raiz on-disk que resolve os paths relativos de
    ``DiscoveredArtifact``; ``None`` trata os paths como estão. O adapter
    nunca lê fora do request, nunca executa conteúdo e nunca abre rede.
    """

    def __init__(self, base_path: str | Path | None = None) -> None:
        self._base = Path(base_path) if base_path is not None else None

    # -- contrato ------------------------------------------------------

    @property
    def domain_id(self) -> str:
        return DOMAIN_ID

    @property
    def adapter_version(self) -> str:
        return ADAPTER_VERSION

    def supported_artifacts(self) -> ArtifactSupport:
        return _SUPPORT

    def entity_kinds(self) -> frozenset[DomainEntityKind]:
        return _ENTITY_KINDS

    def relation_kinds(self) -> frozenset[str]:
        return _RELATION_KINDS

    def confidence_model(self) -> ConfidenceModel:
        # Estritamente evidence-first: o que o texto não prova sai como
        # diagnóstico/unresolved — nunca promovido, nunca inferido.
        return ConfidenceModel(
            ceiling=ConfidenceClass.SYNTACTIC,
            default=ConfidenceClass.SYNTACTIC,
            allows_inferred=False,
            notes=(
                "static AWS artifact extraction; dynamic/ambiguous/"
                "non-literal targets are diagnosed or unresolved"
            ),
        )

    def discovery_rules(self) -> tuple[DiscoveryRule, ...]:
        return _DISCOVERY_RULES

    # -- extract ---------------------------------------------------------

    def extract(self, request: DomainExtractionRequest) -> DomainExtractionResult:
        try:
            return self._extract_all(request)
        except Exception as exc:  # última linha de defesa — nunca levanta
            return DomainExtractionResult(
                domain_id=self.domain_id,
                adapter_version=self.adapter_version,
                diagnostics=(
                    DomainDiagnostic(
                        code="adapter-failure",
                        severity="error",
                        message=f"aws-data adapter failure: {exc!r}",
                        adapter_id=self.domain_id,
                    ),
                ),
            )

    def _extract_all(self, request: DomainExtractionRequest) -> DomainExtractionResult:
        sink = Sink(request, self.adapter_version)
        for art in sorted(request.artifacts, key=lambda a: a.path):
            got = self._read(art.path, art.sha256, sink)
            if got is None:
                continue
            text, sha = got
            try:
                self._dispatch(sink, art.path, text, sha)
            except Exception as exc:  # isolamento por artefato
                sink.diag(
                    "adapter-crash",
                    "error",
                    f"{type(exc).__name__}: {exc}",
                    art.path,
                )
        return sink.finish()

    def _dispatch(self, sink: Sink, path: str, text: str, sha: str) -> None:
        """Roteia por extensão; conteúdo decide quando a forma diverge."""
        suffix = PurePosixPath(path).suffix.lower()
        if path.endswith(".sql.j2"):
            suffix = ".sql"
        if suffix == ".py":
            pyfiles.extract_py(sink, path=path, text=text, sha=sha)
        elif suffix in (".sql", ".hql"):
            if not looks_like_sql(text):
                sink.diag(
                    "not-sql-artifact",
                    "info",
                    "content does not look like SQL — skipped",
                    path,
                )
                return
            extract_sql_fragment(sink, sql_text=text, path=path, sha=sha)
        elif suffix == ".json":
            configs.extract_json(sink, path=path, text=text, sha=sha)
        elif suffix in (".yaml", ".yml"):
            configs.extract_yaml(sink, path=path, text=text, sha=sha)
        elif suffix == ".sh":
            configs.extract_shell(sink, path=path, text=text, sha=sha)
        elif suffix in (".conf", ".properties"):
            configs.extract_spark_conf(sink, path=path, text=text, sha=sha)
        else:
            sink.diag(
                "unsupported-artifact",
                "info",
                f"extension {suffix or '(none)'} not recognized",
                path,
            )

    def _read(self, path: str, sha256: str, sink: Sink) -> tuple[str, str] | None:
        """Lê ``base_path/path``; devolve (texto, sha256) ou None."""
        fs = Path(path) if self._base is None else self._base / path
        try:
            raw = fs.read_bytes()
        except OSError as exc:
            sink.diag(
                "unreadable-artifact",
                "error",
                f"cannot read artifact: {exc}",
                path,
            )
            return None
        if len(raw) > _MAX_ARTIFACT:
            sink.diag(
                "artifact-too-large",
                "error",
                f"{len(raw)} bytes exceeds {_MAX_ARTIFACT}",
                path,
            )
            return None
        real = hashlib.sha256(raw).hexdigest()
        if sha256 and sha256 != real:
            sink.diag(
                "artifact-hash-mismatch",
                "warning",
                f"inventory sha256 {sha256[:12]}… != computed {real[:12]}…",
                path,
            )
        return raw.decode("utf-8", "replace"), real


def satisfies_protocol() -> AwsDataAdapter:
    """Smoke check para tests: a instância satisfaz ``DomainAdapter``."""
    return AwsDataAdapter()
