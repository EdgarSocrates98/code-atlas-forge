"""Contratos do context pack engine (lf-0040 / Cycle 4 Ph6+7).

Determinístico e utilizável sem LLM: seleção por prioridade sobre
evidências persistidas, nunca geração. Níveis: ``orientation``,
``focus``, ``deep`` — outro nível sem reconstruir consulta (pack
carrega ``expansion_refs``).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

PACK_SCHEMA_VERSION = 2

# lf-0121 (Cycle 7 Ph13): a seção ``domain`` é campo ADITIVO de
# ``ContextPack`` — o envelope continua schema v2 (consumidores v2
# ignoram a chave nova); a seção carrega a própria versão.
DOMAIN_PACK_SCHEMA_VERSION = 1

Level = Literal["orientation", "focus", "deep"]

# Prioridade de seleção (spec lf-0040): menor = mais prioritário.
PRIORITY_ESSENTIAL = 0      # evidências essenciais
PRIORITY_DIRECT = 1         # símbolos diretos (match da task)
PRIORITY_CONTRACT = 2       # contratos (defs com docstring/signature)
PRIORITY_DEP = 3            # dependências comprovadas (arestas)
PRIORITY_SNIPPET = 4        # snippets de código
PRIORITY_COMPLEMENT = 5     # complementar (vizinhos transitivos)
PRIORITY_TRANSITIVE = 6     # transitivos menos relevantes


@dataclass(frozen=True)
class ContextRequest:
    """Pedido de pack — snapshot resolvido para o READY atual."""

    task: str                       # query livre da tarefa
    repository_root: str = ""
    snapshot_id: int | None = None
    level: Level = "focus"
    budget_tokens: int = 4000
    max_symbols: int = 50


@dataclass(frozen=True)
class DomainPackRequest:
    """Pedido de pack semeado por entidade de domínio (lf-0121).

    ``seed`` — formas aceitas, nesta ordem:

    - ``dom:<identity_key>`` — chave lógica canônica da entidade
      (``DomainIdentity.logical_key``), escopada ao repositório;
    - ``entity:<row_id>`` (ou dígitos puros) — row id de
      ``domain_entities`` **daquele** repositório;
    - ``<qualified_name>`` — match exato em ``domain_entities`` do repo;
      múltiplos matches viram ``seed-ambiguous`` honesto com as
      ``identity_key``s candidatas — nunca escolha por sorte.

    ``max_neighbors`` limita os vizinhos de ``domain_relations``
    (top-N por confiança); os excedentes viram ``expansion_refs``
    re-consultáveis — nunca silenciados.
    """

    seed: str
    repository_root: str = ""
    snapshot_id: int | None = None
    level: Level = "focus"
    budget_tokens: int = 4000
    max_neighbors: int = 20


@dataclass(frozen=True)
class PackEvidence:
    """Referência a evidência persistida — nunca inventada."""

    kind: str                       # "occurrence"|"relation"|"diagnostic"
    ref: str                        # id/canônico
    detail: str = ""


@dataclass(frozen=True)
class ContextItem:
    """Unidade de contexto com custo estimado e prioridade."""

    item_id: str                    # estável p/ expansão/dedup
    kind: str                       # "symbol"|"file"|"dependency"|"evidence"|"snippet"
    priority: int                   # PRIORITY_*
    title: str
    body: str                       # texto indexável/contábil
    est_tokens: int
    symbol_key: str = ""
    path: str = ""
    evidence: tuple[PackEvidence, ...] = ()
    expandable: bool = False        # tem refs p/ nível mais profundo


@dataclass(frozen=True)
class ExpansionRef:
    """Referência para disclosure progressivo (outro nível)."""

    ref_id: str
    kind: str                       # "symbol"|"file"|"dependency"
    reason: str                     # por que está disponível p/ expandir


@dataclass(frozen=True)
class Budget:
    """Orçamento declarado + reservas do envelope."""

    limit: int
    reserved_meta: int
    reserved_evidence: int
    reserved_diagnostics: int
    available_items: int            # limit - reservas
    estimator: str                  # id do estimador (heurístico)


@dataclass(frozen=True)
class PackDiagnostics:
    """Diagnósticos tipados do build."""

    codes: tuple[str, ...] = ()
    messages: tuple[str, ...] = ()
    insufficient_budget: bool = False
    mandatory_context_omitted: bool = False
    suggested_minimum_budget: int = 0


@dataclass(frozen=True)
class PackManifest:
    """Metadados do pack — auditoria do build."""

    schema_version: int
    repository: str
    snapshot_id: int
    level: Level
    estimator: str
    tokens_estimated: bool          # True — nunca contagem exata
    selected: int
    omitted: int
    truncated: bool
    freshness: str                  # id do snapshot + estado
    ranking_explanation: str
    tokens_payload: int = 0         # estimativa do payload serializado


# ---------------------------------------------------------------------
# lf-0121 — seção de domínio (aditiva; envelope segue schema v2)
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class DomainEntityCard:
    """Cartão da entidade-semente — identidade + ancoragem no snapshot.

    ``identity_key`` é o ref re-consultável (ver ``requery`` da seção).
    """

    entity_id: int                  # row id em domain_entities
    occurrence_id: int              # 1ª ocorrência da entidade no snapshot
    occurrence_count: int
    domain: str
    kind: str                       # DomainEntityKind (data_table, …)
    name: str
    qualified_name: str
    identity_key: str               # dom:… lógica — ref estável
    repository_id: int
    snapshot_id: int
    catalog: str = ""
    namespace: str = ""
    environment: str = ""
    source_path: str = ""
    extraction_status: str = ""


@dataclass(frozen=True)
class DomainNeighborRef:
    """Vizinho de domínio incluído no pack — espelho estruturado do item.

    ``ref`` é o identificador re-consultável: ``dom:<identity_key>``
    para entidades (mesmo valor do ``ref_id`` de ``expansion_refs``) ou
    ``sym:<qualified_name>`` para símbolo de código (ponte
    domínio↔código via ``*_symbol_occ_id``).
    """

    item_id: str                    # == ref — item carrega o ref como id
    ref: str                        # dom:… | sym:<qualified_name>
    kind: str                       # "entity" | "symbol"
    entity_kind: str                # data_table… | kind do símbolo
    display: str                    # qualified_name legível
    direction: str                  # incoming | outgoing | both
    relation_types: tuple[str, ...]
    resolution_status: str          # status da aresta de maior confiança
    confidence_class: str           # classe de confiança mais forte
    edge_count: int
    evidence_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class DomainPackSection:
    """Seção aditiva de domínio no ``ContextPack`` (schema próprio v1).

    ``status`` é a palavra honesta do build:

    - ``ok`` — seed resolvida, seção completa;
    - ``absent`` — snapshot sem ocorrências de domínio (nenhuma entidade
      observada — repo indexado sem adapters de domínio ou snapshot
      anterior à v9);
    - ``seed-not-found`` — seed não casa nenhuma entidade do repo;
    - ``seed-ambiguous`` — seed por ``qualified_name`` casou >1
      entidade; ``candidates`` lista as ``identity_key``s;
    - ``seed-not-in-snapshot`` — entidade existe no repo mas sem
      ocorrência neste snapshot.

    ``counts`` agrega TODAS as arestas do snapshot que tocam a seed
    (não só as incluídas): ``incoming``/``outgoing`` por direção,
    ``resolved``/``ambiguous``/``external`` por status,
    ``unresolved`` exclui alvos com marcador de honestidade
    (``dynamic:``, ``${dynamic}``, ``environment:``) — esses contam em
    ``dynamic`` à parte.
    """

    schema_version: int
    status: str
    entity: DomainEntityCard | None
    neighbors: tuple[DomainNeighborRef, ...]    # incluídos (top-N ∩ budget)
    neighbors_total: int                        # candidatos dedupados
    neighbors_omitted: int                      # total - incluídos
    counts: Mapping[str, int]
    unresolved_targets: tuple[str, ...]         # amostra de alvos pendentes
    ambiguous_targets: tuple[str, ...]          # amostra de alvos ambíguos
    summary: str                                # domains.summarize — fatos
    requery: str                                # contrato dos expansion refs
    candidates: tuple[str, ...] = ()            # seeds ambíguas


@dataclass(frozen=True)
class DomainPackPart:
    """Saída do ``domain_factory`` — construída DENTRO do loop de
    enforcement de budget, logo tudo aqui entra na estimativa do
    payload serializado."""

    section: DomainPackSection
    extra_expansion_refs: tuple[ExpansionRef, ...] = ()  # vizinhos além do top-N
    extra_unresolved: tuple[str, ...] = ()               # → unresolved_refs
    ranking_explanation: str = ""                        # override do manifest


DomainSectionFactory = Callable[[tuple[ContextItem, ...]], "DomainPackPart | None"]
"""Monta a seção a partir dos itens SELECIONADOS de uma iteração do
enforcement — garante que ``section.neighbors`` espelha exatamente o
que o pack entrega (nunca anuncia vizinho despejado por budget)."""


@dataclass(frozen=True)
class ContextPack:
    """Envelop versionado — o artefato final do engine.

    ``domain`` é aditivo (lf-0121): ``None`` para packs de símbolo;
    ``DomainPackSection`` para packs semeados por entidade de domínio.
    """

    manifest: PackManifest
    budget: Budget
    items: tuple[ContextItem, ...]
    selected_symbols: tuple[str, ...]
    relevant_files: tuple[str, ...]
    dependencies: tuple[PackEvidence, ...]
    unresolved_refs: tuple[str, ...]
    expansion_refs: tuple[ExpansionRef, ...]
    diagnostics: PackDiagnostics
    domain: DomainPackSection | None = None

    def to_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)
