"""Contrato versionado dos adaptadores de domínio (lf-0110, Cycle 7).

Um ``DomainAdapter`` observa artefatos **não-código** do repositório
(SQL, PySpark, OpenAPI, Terraform, CI/CD…) e extrai entidades/relações
tipadas para o grafo unificado de domínio. Mesma disciplina dos
``ParserAdapter``/``SemanticResolver``:

- **evidence-first**: todo fato carrega ``EvidenceRecord`` apontando
  para conteúdo real do inventário — nada é inventado;
- **sem execução**: adapters nunca executam código, jobs, terraform ou
  pipelines e nunca abrem conexões externas (ADR-0014);
- **resolução honesta**: alvos usam ``DomainResolution`` — ``inferred``
  nunca vira ``resolved``;
- **diagnósticos, nunca exceções**: ``extract`` não levanta para
  conteúdo ruim — registra ``DomainDiagnostic`` no resultado.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from code_atlas.contracts.enums import ConfidenceClass
from code_atlas.domains.identity import DomainIdentity
from code_atlas.domains.model import DomainEntityKind
from code_atlas.domains.resolution import DomainResolution
from code_atlas.parsers.contracts import EvidenceRecord, SourceSpan

DOMAIN_CONTRACT_VERSION = 1

# Ordem de força da evidência — espelha a precedência de resolvers
# (semantic > syntactic > heuristic > inferred; ADR-0021).
_CONFIDENCE_RANK: dict[ConfidenceClass, int] = {
    ConfidenceClass.INFERRED: 0,
    ConfidenceClass.HEURISTIC: 1,
    ConfidenceClass.SYNTACTIC: 2,
    ConfidenceClass.RESOLVED: 3,
}


def confidence_rank(cls: ConfidenceClass) -> int:
    """Rank ordinal da classe de confiança (maior = evidência mais forte)."""
    return _CONFIDENCE_RANK[ConfidenceClass(cls)]


@dataclass(frozen=True)
class ArtifactSupport:
    """Artefatos que um adapter consegue interpretar.

    ``extensions``: sufixos lowercase com ponto (ex.: ``frozenset({".sql",
    ".tf"})``). ``globs``: padrões ``fnmatch`` sobre o path relativo
    POSIX (ex.: ``"**/migrations/*.sql"``); ``"**"`` não tem semântica
    recursiva especial no fnmatch stdlib — escrever padrões explícitos.
    """

    extensions: frozenset[str] = frozenset()
    globs: tuple[str, ...] = ()

    def matches(self, path: str) -> bool:
        """True se ``path`` (relativo POSIX) é artefato suportado."""
        if not path:
            return False
        lower = path.lower()
        dot = lower.rfind(".")
        if dot >= 0 and lower[dot:] in self.extensions:
            return True
        return any(fnmatch.fnmatchcase(path, g) for g in self.globs)


@dataclass(frozen=True)
class DiscoveryRule:
    """Regra declarativa de descoberta de artefatos do domínio.

    Descreve *onde* o domínio costuma viver — o scanner/coordinator pode
    priorizar ou rotular artefatos por regra sem rodar o adapter.
    """

    glob: str                     # fnmatch sobre path relativo POSIX
    artifact_kind: str            # rótulo: "sql-script", "tf-module", …
    required: bool = False        # False = artefato opcional/ausente é ok
    notes: str = ""


@dataclass(frozen=True)
class ConfidenceModel:
    """Política de confiança declarada pelo adapter.

    ``ceiling``: classe máxima que o adapter pode emitir **sem** passar
    por um resolvedor externo. Default ``syntactic`` — extração local
    honesta; ``resolved`` exige evidência de índice/resolver (ADR-0021).
    ``allows_inferred=False`` proíbe o adapter de emitir ``inferred``
    (adapters estritamente evidence-first devem recusar, não inferir).
    """

    ceiling: ConfidenceClass = ConfidenceClass.SYNTACTIC
    default: ConfidenceClass = ConfidenceClass.SYNTACTIC
    allows_inferred: bool = True
    notes: str = ""

    def __post_init__(self) -> None:
        for f in ("ceiling", "default"):
            object.__setattr__(
                self, f, ConfidenceClass(getattr(self, f))
            )
        if _CONFIDENCE_RANK[self.default] > _CONFIDENCE_RANK[self.ceiling]:
            raise ValueError("confidence default cannot exceed ceiling")

    def admits(self, cls: ConfidenceClass) -> bool:
        """True se a classe respeita o teto e a política de inferência."""
        cls = ConfidenceClass(cls)
        if cls is ConfidenceClass.INFERRED and not self.allows_inferred:
            return False
        return _CONFIDENCE_RANK[cls] <= _CONFIDENCE_RANK[self.ceiling]


@dataclass(frozen=True)
class DiscoveredArtifact:
    """Artefato concreto encontrado no repositório."""

    path: str                     # relativo POSIX
    artifact_kind: str = ""       # rótulo vindo da DiscoveryRule, se houver
    sha256: str = ""              # hash do inventário, quando conhecido


@dataclass(frozen=True)
class DomainExtractionRequest:
    """Entrada de extração — recorte estável do workspace.

    ``repository`` é a ``identity_key`` lógica (lf-0021); ``snapshot`` é
    um rótulo de revisão (commit/digest) opcional — a ligação com a row
    ``snapshots.id`` é feita pelo coordinator, não pelo adapter.
    ``root`` isola sub-projetos de monorepo (entra na identidade).
    """

    repository: str
    artifacts: tuple[DiscoveredArtifact, ...] = ()
    snapshot: str = ""
    root: str = ""
    environment: str = ""


@dataclass(frozen=True)
class ExtractedDomainEntity:
    """Entidade extraída por um adapter — ainda sem row ids.

    ``path`` é o artefato de origem ("" só para entidades sintetizadas
    por derivação declarada). ``evidence`` são índices em
    ``DomainExtractionResult.evidence`` — mesmo desenho posicional de
    ``GraphEdge.evidence_index``.
    """

    identity: DomainIdentity
    kind: DomainEntityKind
    name: str
    path: str = ""
    span: SourceSpan | None = None
    content_hash: str = ""
    attributes: Mapping[str, str] = field(default_factory=dict)
    evidence: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "kind", DomainEntityKind(self.kind)
        )


@dataclass(frozen=True)
class ExtractedDomainRelation:
    """Relação candidata entre endpoints de domínio ou código.

    Origem: ``from_identity`` (entidade de domínio) **ou**
    ``from_symbol`` (qualified name de símbolo de código — a ponte
    código→domínio). Alvo: ``to_identity``, ``to_symbol`` ou
    ``to_unresolved`` (texto honesto). ``resolution`` carrega o estado
    declarado pelo adapter; quando ``None`` o coordinator trata como
    ``unresolved`` + ``syntactic``.
    """

    relation_type: str
    path: str                     # artefato de origem da observação
    from_identity: DomainIdentity | None = None
    from_symbol: str = ""         # qualified name do símbolo de origem
    to_identity: DomainIdentity | None = None
    to_symbol: str = ""
    to_unresolved: str = ""
    resolution: DomainResolution | None = None
    span: SourceSpan | None = None
    evidence: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if self.from_identity is None and not self.from_symbol:
            raise ValueError("extracted relation requires a from endpoint")
        if (
            self.to_identity is None
            and not self.to_symbol
            and not self.to_unresolved
        ):
            raise ValueError(
                "extracted relation requires to endpoint or to_unresolved"
            )


@dataclass(frozen=True)
class DomainDiagnostic:
    """Diagnóstico de adapter (artefato inválido, ambiguidade, recusa)."""

    code: str                     # ex.: "unsupported-artifact", "parse-error"
    severity: str                 # info | warning | error
    message: str
    path: str = ""
    adapter_id: str = ""


@dataclass(frozen=True)
class DomainExtractionResult:
    """Envelope versionado da saída de ``DomainAdapter.extract``."""

    domain_id: str
    adapter_version: str
    schema_version: int = DOMAIN_CONTRACT_VERSION
    entities: tuple[ExtractedDomainEntity, ...] = ()
    relations: tuple[ExtractedDomainRelation, ...] = ()
    evidence: tuple[EvidenceRecord, ...] = ()
    diagnostics: tuple[DomainDiagnostic, ...] = ()
    stats: Mapping[str, int] = field(default_factory=dict)


@runtime_checkable
class DomainAdapter(Protocol):
    """Interface implementada por qualquer adapter de domínio.

    Adapters são puros: recebem artefatos descobertos, devolvem
    entidades/relações/evidência — nunca leem disco fora do request,
    nunca executam conteúdo, nunca abrem rede.
    """

    @property
    def domain_id(self) -> str:
        """Id estável do domínio (``sql``, ``pyspark``, ``terraform``…)."""
        ...

    @property
    def adapter_version(self) -> str:
        """Versão do adapter (semver interno) — entra em analyzer_versions."""
        ...

    def supported_artifacts(self) -> ArtifactSupport:
        """Extensões/globs que o adapter interpreta."""
        ...

    def entity_kinds(self) -> frozenset[DomainEntityKind]:
        """Subconjunto de ``DomainEntityKind`` que o adapter emite."""
        ...

    def relation_kinds(self) -> frozenset[str]:
        """Subconjunto do vocabulário de relações que o adapter emite."""
        ...

    def confidence_model(self) -> ConfidenceModel:
        """Teto/default de confiança e política de inferência."""
        ...

    def discovery_rules(self) -> tuple[DiscoveryRule, ...]:
        """Regras declarativas de onde os artefatos do domínio vivem."""
        ...

    def extract(
        self, request: DomainExtractionRequest
    ) -> DomainExtractionResult:
        """Extrai entidades/relações dos artefatos — nunca levanta para
        conteúdo ruim; falhas viram ``DomainDiagnostic``."""
        ...
