"""Modelo unificado de entidades/relações de domínio (lf-0110, Cycle 7).

Vocabulário fechado do prompt Cycle 7 (Phases 1+2): adapters extraem
**somente** estes kinds — qualquer extensão exige spec + ADR. ``TESTS`` e
``REFERENCES`` são compartilhados com ``EdgeKind`` (mesmo valor semântico).

``REPOSITORY``/``PROJECT`` aqui são entidades de **domínio** (ex.: um repo
federado referenciado por um workflow CI/CD), distintos de
``NodeKind.REPOSITORY`` — nó do grafo de código já coberto pelo modelo de
símbolos. Camadas diferentes, tabelas diferentes.

Persistência (ADR-0010): entidades de domínio NÃO reutilizam a tabela
``symbols`` — a identidade delas inclui ``catalog``/``namespace``/
``environment``, que o UNIQUE de ``symbols`` (repo, language, qname, kind)
não consegue expressar (duas tabelas ``orders`` de catálogos distintos
colidiriam). Schema dedicado na migração v9.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from code_atlas.contracts.enums import ConfidenceClass, EdgeKind, ResolutionStatus
from code_atlas.contracts.models import _coerce
from code_atlas.domains.identity import DomainIdentity


class DomainEntityKind(StrEnum):
    """Tipos de entidade de domínio (prompt Cycle 7 — vocabulário fechado)."""

    DATA_TABLE = "data_table"
    DATA_COLUMN = "data_column"
    DATA_VIEW = "data_view"
    DATA_TRANSFORMATION = "data_transformation"
    DATA_PIPELINE = "data_pipeline"
    SPARK_JOB = "spark_job"
    GLUE_JOB = "glue_job"
    DATA_CATALOG = "data_catalog"
    ICEBERG_TABLE = "iceberg_table"
    API_ENDPOINT = "api_endpoint"
    API_SERVICE = "api_service"
    API_CONTRACT = "api_contract"
    TERRAFORM_RESOURCE = "terraform_resource"
    TERRAFORM_MODULE = "terraform_module"
    IAM_ROLE = "iam_role"
    CICD_WORKFLOW = "cicd_workflow"
    CICD_JOB = "cicd_job"
    REPOSITORY = "repository"  # entidade de federação (lf-0118), ≠ NodeKind
    PROJECT = "project"


class DomainRelationKind(StrEnum):
    """Relações de domínio (prompt Cycle 7 — vocabulário fechado)."""

    READS_FROM = "READS_FROM"
    WRITES_TO = "WRITES_TO"
    TRANSFORMS = "TRANSFORMS"
    DERIVES_FROM = "DERIVES_FROM"
    DEPENDS_ON = "DEPENDS_ON"
    CALLS_ENDPOINT = "CALLS_ENDPOINT"
    IMPLEMENTS_CONTRACT = "IMPLEMENTS_CONTRACT"
    PROVISIONS = "PROVISIONS"
    USES_RESOURCE = "USES_RESOURCE"
    ASSUMES_ROLE = "ASSUMES_ROLE"
    TRIGGERS = "TRIGGERS"
    DEPLOYS = "DEPLOYS"
    TESTS = "TESTS"          # valor compartilhado com EdgeKind.TESTS
    REFERENCES = "REFERENCES"  # valor compartilhado com EdgeKind.REFERENCES


# Vocabulário aceito em domain_relations.type: as relações de domínio mais
# o vocabulário estrutural (CONTAINS módulo→recurso, IMPORTS de tf module…).
DOMAIN_RELATION_TYPES: frozenset[str] = frozenset(
    {str(k) for k in DomainRelationKind} | {str(k) for k in EdgeKind}
)


@dataclass(frozen=True)
class DomainEntity:
    """Entidade lógica de domínio — estável por repositório.

    ``id`` é a row id em ``domain_entities`` (``None`` = a persistir);
    ``attributes`` carrega observáveis específicos do domínio (ddl
    resumido, tipo de recurso, provider) como strings — nada de blobs
    arbitrários nem segredos.
    """

    identity: DomainIdentity
    kind: DomainEntityKind
    name: str
    attributes: Mapping[str, str] = field(default_factory=dict)
    id: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "kind", _coerce(DomainEntityKind, self.kind, "kind")
        )
        if not self.name.strip():
            raise ValueError("domain entity requires name")
        object.__setattr__(
            self,
            "attributes",
            {str(k): str(v) for k, v in self.attributes.items()},
        )

    @property
    def domain(self) -> str:
        return self.identity.domain

    @property
    def qualified_name(self) -> str:
        return self.identity.qualified_name

    def identity_key(self) -> str:
        """Chave lógica persistida em ``domain_entities.identity_key``."""
        return self.identity.logical_key()


@dataclass(frozen=True)
class DomainEntityOccurrence:
    """Ocorrência de uma entidade num snapshot (span/hash da ocorrência).

    ``source_file_id=None`` é permitido apenas para entidades sintetizadas
    por derivação declarada (ex.: catálogo agregado) — o caso normal é
    evidence-first: entidade observada num arquivo do inventário.
    """

    snapshot_id: int
    entity_id: int
    source_file_id: int | None
    id: int | None = None
    span_start: int | None = None
    span_end: int | None = None
    line: int | None = None
    col: int | None = None
    content_hash: str | None = None
    attributes: Mapping[str, str] = field(default_factory=dict)
    extraction_status: str = "extracted"  # extracted | partial | failed


@dataclass(frozen=True)
class DomainRelation:
    """Aresta de domínio com proveniência obrigatória (espelha ``Relation``).

    Endpoints podem ser ocorrências de entidade de domínio OU de símbolo
    de código — a ponte código↔domínio (lf-0117/0120) precisa das duas
    direções sem nova migração. Exige-se ≥1 endpoint de origem e ≥1 alvo
    (ou ``to_unresolved`` honesto).

    Invariante inegociável (spec): evidência ``inferred`` NUNCA é
    promovida a ``resolved``.
    """

    snapshot_id: int
    relation_type: str
    resolver: str
    confidence_class: ConfidenceClass
    resolution_status: ResolutionStatus
    id: int | None = None
    from_entity_occ_id: int | None = None
    from_symbol_occ_id: int | None = None
    to_entity_occ_id: int | None = None
    to_symbol_occ_id: int | None = None
    to_unresolved: str | None = None
    evidence_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        t = str(self.relation_type)
        if t not in DOMAIN_RELATION_TYPES:
            raise ValueError(f"invalid relation_type: {self.relation_type!r}")
        object.__setattr__(self, "relation_type", t)
        object.__setattr__(
            self,
            "confidence_class",
            _coerce(ConfidenceClass, self.confidence_class, "confidence_class"),
        )
        object.__setattr__(
            self,
            "resolution_status",
            _coerce(ResolutionStatus, self.resolution_status, "resolution_status"),
        )
        if self.from_entity_occ_id is None and self.from_symbol_occ_id is None:
            raise ValueError("domain relation requires a from endpoint")
        if (
            self.to_entity_occ_id is None
            and self.to_symbol_occ_id is None
            and not self.to_unresolved
        ):
            raise ValueError(
                "domain relation requires to endpoint or to_unresolved"
            )
        if not self.resolver:
            raise ValueError("domain relation requires resolver provenance")
        if (
            self.confidence_class is ConfidenceClass.INFERRED
            and self.resolution_status is ResolutionStatus.RESOLVED
        ):
            raise ValueError(
                "inferred evidence can never be reported resolved"
            )
