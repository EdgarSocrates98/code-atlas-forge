"""Contratos do Impact Engine (Cycle 5 Phase 1).

Dois eixos separados — nunca um único ``confidence``:
- ``FindingKind``: o que o achado É (mudança, dependência, risco, teste).
- ``EvidenceStrength``: quão comprovado está (verified > supported >
  possible > unknown). Uma aresta incerta não promove a cadeia.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

IMPACT_SCHEMA_VERSION = 1
ANALYZER_VERSION = "impact-v1"


class FindingKind(StrEnum):
    OBSERVED_CHANGE = "observed_change"
    DEPENDENCY = "dependency"
    COMPATIBILITY_RISK = "compatibility_risk"
    TEST_RELEVANCE = "test_relevance"
    UNKNOWN = "unknown"


class EvidenceStrength(StrEnum):
    VERIFIED = "verified"
    SUPPORTED = "supported"
    POSSIBLE = "possible"
    UNKNOWN = "unknown"


class ChangeKind(StrEnum):
    """Tipos de mudança de arquivo/símbolo (Phase 2+3)."""

    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    COPIED = "copied"
    MODE_CHANGED = "mode_changed"
    TYPE_CHANGED = "type_changed"
    BINARY = "binary"
    UNAVAILABLE = "unavailable"


class SymbolChangeKind(StrEnum):
    """Classificação semântica por símbolo (Phase 3)."""

    ADDED = "added"
    REMOVED = "removed"
    RENAMED = "renamed"
    MOVED = "moved"
    SIGNATURE_CHANGED = "signature_changed"
    BODY_CHANGED = "body_changed"
    DOC_CHANGED = "doc_changed"
    NO_SEMANTIC_CHANGE = "no_semantic_change"
    UNKNOWN = "unknown"


class ImpactKind(StrEnum):
    DIRECT = "direct"
    TRANSITIVE = "transitive"
    POSSIBLE = "possible"
    UNKNOWN = "unknown"


class RiskCategory(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FileChange:
    """Uma entrada do ChangeSet (Phase 2)."""

    kind: ChangeKind
    old_path: str | None
    new_path: str | None
    old_hash: str | None = None
    new_hash: str | None = None
    similarity: int | None = None   # 0-100 em renomes/cópias git
    binary: bool = False


@dataclass(frozen=True)
class ChangeSet:
    """Conjunto de alterações entre duas revisões (Phase 2)."""

    base_revision: str
    target_revision: str
    files: tuple[FileChange, ...]
    source: str = "git"             # git | snapshots | explicit
    dirty_worktree: bool = False
    commit_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SymbolChange:
    """Mudança detectada num símbolo entre dois snapshots (Phase 3)."""

    symbol_key: str                 # lang:kind:qname (mesma forma do search)
    qualified_name: str
    language: str
    kind: str
    change: SymbolChangeKind
    strength: EvidenceStrength
    old_signature: str | None = None
    new_signature: str | None = None
    old_path: str | None = None
    new_path: str | None = None
    old_occurrence_id: int | None = None
    new_occurrence_id: int | None = None
    detail: str = ""


@dataclass(frozen=True)
class ContractChange:
    """Alteração de contrato público classificada (Phase 4)."""

    symbol_key: str
    classification: str             # ex.: required-parameter-added
    compatibility: str              # potential-risk | likely-compatible
                                    # | no-contract-impact | indeterminate
    old_signature: str | None
    new_signature: str | None
    strength: EvidenceStrength
    detail: str = ""


@dataclass(frozen=True)
class ImpactPath:
    """Cadeia auditável origem→afetado (Phase 5)."""

    origin_symbol_key: str
    affected_symbol_key: str
    hops: int
    relation_ids: tuple[int, ...]
    intermediate_keys: tuple[str, ...]
    resolution_statuses: tuple[str, ...]
    fully_verified: bool
    reason: str
    # lf-0120: refs tipadas por hop ("rel:<id>" = relations,
    # "dom:<id>" = domain_relations) — os ids numéricos das duas
    # tabelas compartilham espaço, então o prefixo preserva a
    # proveniência quando o caminho mistura código e domínio.
    edge_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImpactCandidate:
    """Símbolo/arquivo potencialmente impactado (Phase 5)."""

    symbol_key: str
    qualified_name: str
    language: str
    kind: str
    impact: ImpactKind
    strength: EvidenceStrength
    path: ImpactPath | None
    source_path: str | None = None
    occurrence_id: int | None = None
    # lf-0078: estado do consumidor histórico no destino
    # ("" | still_present | also_removed | unresolved_in_target)
    consumer_state: str = ""
    # lf-0120 (Cycle 7 Phase 12): tipo do nó impactado — ``"symbol"``
    # para símbolo de código, ``"domain"`` para entidade de domínio
    # (o kind então carrega o ``DomainEntityKind``: data_table,
    # spark_job…). ``symbol_key`` de entidade é a ``identity_key``
    # canônica ``dom:…`` e ``language`` é o domínio produtor.
    node_type: str = "symbol"
    # Ocorrência de ``domain_entity_occurrences`` — espaço de ids
    # separado de ``occurrence_id`` (que é sempre symbol_occurrences).
    domain_occurrence_id: int | None = None
    # Dependências não-traversáveis do nó (alvos ``unresolved`` /
    # ``ambiguous`` / ``external`` sem endpoint concreto) — contam como
    # incerteza, nunca como blast confirmado. Formas curtas estáveis:
    # ``"WRITES_TO→<texto> (unresolved)"``.
    uncertain_deps: tuple[str, ...] = ()


@dataclass(frozen=True)
class TestCandidate:
    """Teste candidato relacionado à mudança (Phase 7)."""

    test_file: str
    test_symbol: str | None
    relationship: str               # direct | transitive | heuristic | unknown
    reason: str
    strength: EvidenceStrength
    occurrence_id: int | None = None
    revision: str = ""              # lf-0080: "base" | "target" | ""


@dataclass(frozen=True)
class RiskAssessment:
    """Avaliação determinística de risco (Phase 6)."""

    category: RiskCategory
    uncertainty: str                # low|moderate|high — separado do risco
    reasons: tuple[str, ...]
    formula_version: str = "risk-rules-v1"


@dataclass(frozen=True)
class ImpactDiagnostic:
    code: str
    message: str


@dataclass(frozen=True)
class ImpactLimits:
    max_depth: int = 4
    max_nodes: int = 500
    max_paths: int = 50
    max_changes: int = 500


@dataclass(frozen=True)
class ImpactRequest:
    """Pedido de análise — todas as superfícies (CLI/MCP) viram isto."""

    repository_root: str
    base_revision: str = ""         # HEAD~1, sha, branch, snapshot:N
    target_revision: str = ""       # vazio = worktree
    symbol: str | None = None       # seleção explícita (também aceita
                                    # ref de entidade — ver ``entity``)
    file: str | None = None
    # lf-0120 (Cycle 7 Phase 12): seed de entidade de domínio — a
    # análise passa a responder "se esta tabela/job/recurso mudar".
    # Formas aceitas: ``identity_key`` canônica ``dom:…``, row id
    # (``entity:N``/``id:N``/``N``), ``display()`` (ex.:
    # ``pyspark:staging.orders``) ou nome qualificado
    # (``staging.orders``, ``orders``). ``symbol`` também funciona como
    # seed de entidade quando casa — fiação mínima para handlers que já
    # expõem ``symbol`` (API/CLI/MCP não precisam mudar).
    entity: str | None = None
    base_snapshot_id: int | None = None
    target_snapshot_id: int | None = None
    include_possible: bool = True
    known_only: bool = False
    limits: ImpactLimits = field(default_factory=ImpactLimits)


@dataclass(frozen=True)
class ImpactResult:
    """Resposta canônica — Phase 1 exige todos os campos identificáveis."""

    schema_version: int
    analyzer_version: str
    repository: str
    base_revision: str
    target_revision: str
    base_snapshot_id: int | None
    target_snapshot_id: int | None
    change_set: ChangeSet | None
    symbol_changes: tuple[SymbolChange, ...]
    contract_changes: tuple[ContractChange, ...]
    impacts: tuple[ImpactCandidate, ...]
    test_candidates: tuple[TestCandidate, ...]
    risk: RiskAssessment
    unknown_coverage: tuple[str, ...]
    truncated: bool
    diagnostics: tuple[ImpactDiagnostic, ...]


@dataclass(frozen=True)
class ImpactReport:
    """Envelope de relatório (Phase 9) — renderers JSON/MD/CLI."""

    schema_version: int
    result: ImpactResult
    format: str                     # json | markdown | cli
