"""``PySparkAdapter`` — adapter de domínio PySpark (lf-0112, Cycle 7 Ph.4).

Linhagem estática de DataFrames sobre artefatos ``.py`` — análise
``ast`` pura, zero execução, zero imports de ``pyspark``:

- sessões: ``spark``/``spark_session``/``sparkSession`` implícitas
  (notebook), ``SparkSession.builder…getOrCreate()``,
  ``getActiveSession()`` e ``self.spark``;
- leituras: ``spark.read.table|parquet|csv|json|jdbc|format(…).load``,
  ``spark.table`` — tabelas de catálogo e paths nunca se confundem;
- SQL: ``spark.sql("literal")`` delega ao parser/extrator do adapter
  ``sql`` (lf-0111) — CTEs colapsam, ``SELECT *`` é diagnóstico, e o
  resultado é re-ancorado na transformação do call-site;
- transforms: ``select/filter/join/groupBy/agg/union/withColumn/
  dropDuplicates/withColumnRenamed`` + família completa de ops
  ``DataFrame → DataFrame``;
- writes: ``df.write.parquet|saveAsTable|insertInto|mode(…).<fmt>``,
  ``df.writeTo(t).<terminal>`` (Iceberg-style, ``iceberg_table``);
- honestidade: alvos dinâmicos (``f-string``, variáveis não-literal),
  receivers desconhecidos e SQL dinâmico viram ``to_unresolved`` +
  diagnóstico — nenhuma relação fabricada, nenhum ``resolved`` local.

Leitura de artefatos (mesmo desenho do ``SqlDomainAdapter``):
``base_path`` resolve os paths relativos de ``DiscoveredArtifact``;
``None`` trata os paths como estão. O adapter nunca lê fora do
request, nunca executa jobs e nunca abre rede.
"""

from __future__ import annotations

from pathlib import Path

from code_atlas.contracts.enums import ConfidenceClass, EdgeKind
from code_atlas.domains.contracts import (
    ArtifactSupport,
    ConfidenceModel,
    DiscoveryRule,
    DomainDiagnostic,
    DomainExtractionRequest,
    DomainExtractionResult,
)
from code_atlas.domains.model import DomainEntityKind, DomainRelationKind
from code_atlas.domains.spark.extract import extract as _extract

DOMAIN_ID = "pyspark"
ADAPTER_VERSION = "0.1.0"


class PySparkAdapter:
    """Adapter ``pyspark`` — linhagem estática de DataFrames."""

    def __init__(self, base_path: str | Path | None = None) -> None:
        self._base = (
            Path(base_path) if base_path is not None else Path.cwd()
        )

    # -- contrato ------------------------------------------------------

    @property
    def domain_id(self) -> str:
        return DOMAIN_ID

    @property
    def adapter_version(self) -> str:
        return ADAPTER_VERSION

    def supported_artifacts(self) -> ArtifactSupport:
        return ArtifactSupport(extensions=frozenset({".py"}))

    def entity_kinds(self) -> frozenset[DomainEntityKind]:
        return frozenset(
            {
                DomainEntityKind.DATA_TABLE,
                DomainEntityKind.DATA_VIEW,
                DomainEntityKind.DATA_COLUMN,
                DomainEntityKind.ICEBERG_TABLE,
                DomainEntityKind.DATA_TRANSFORMATION,
                DomainEntityKind.SPARK_JOB,
            }
        )

    def relation_kinds(self) -> frozenset[str]:
        return frozenset(
            {
                str(DomainRelationKind.READS_FROM),
                str(DomainRelationKind.WRITES_TO),
                str(DomainRelationKind.TRANSFORMS),
                str(DomainRelationKind.DERIVES_FROM),
                # job CONTAINS transformation — vocabulário estrutural
                # compartilhado (EdgeKind) permitido pelo schema v9
                str(EdgeKind.CONTAINS),
            }
        )

    def confidence_model(self) -> ConfidenceModel:
        # Extração estritamente evidence-first: o que o AST não
        # estabelece sai como diagnóstico/unresolved — nunca promovido.
        return ConfidenceModel(
            ceiling=ConfidenceClass.SYNTACTIC,
            default=ConfidenceClass.SYNTACTIC,
            allows_inferred=False,
            notes="static PySpark DataFrame lineage; dynamic sql/paths/"
            "unresolvable receivers are diagnosed, never resolved",
        )

    def discovery_rules(self) -> tuple[DiscoveryRule, ...]:
        return (
            DiscoveryRule(
                glob="*.py",
                artifact_kind="pyspark-job",
                notes=(
                    "Python files are scanned for SparkSession usage "
                    "(spark.read/spark.sql/df.write) — non-Spark .py "
                    "files are ignored silently"
                ),
            ),
        )

    # -- extract ---------------------------------------------------------

    def extract(
        self, request: DomainExtractionRequest
    ) -> DomainExtractionResult:
        try:
            return _extract(
                request, self.adapter_version, self._base
            )
        except Exception as exc:  # última linha de defesa — nunca levanta
            return DomainExtractionResult(
                domain_id=self.domain_id,
                adapter_version=self.adapter_version,
                diagnostics=(
                    DomainDiagnostic(
                        code="adapter-failure",
                        severity="error",
                        message=f"pyspark adapter failure: {exc!r}",
                        adapter_id=self.domain_id,
                    ),
                ),
            )


def satisfies_protocol() -> PySparkAdapter:
    """Smoke check para tests: a instância satisfaz ``DomainAdapter``."""
    adapter: PySparkAdapter = PySparkAdapter()
    return adapter
