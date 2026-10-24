"""Contratos do context pack engine (lf-0040 / Cycle 4 Ph6+7).

Determinístico e utilizável sem LLM: seleção por prioridade sobre
evidências persistidas, nunca geração. Níveis: ``orientation``,
``focus``, ``deep`` — outro nível sem reconstruir consulta (pack
carrega ``expansion_refs``).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Literal

PACK_SCHEMA_VERSION = 2

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


@dataclass(frozen=True)
class ContextPack:
    """Envelop versionado — o artefato final do engine."""

    manifest: PackManifest
    budget: Budget
    items: tuple[ContextItem, ...]
    selected_symbols: tuple[str, ...]
    relevant_files: tuple[str, ...]
    dependencies: tuple[PackEvidence, ...]
    unresolved_refs: tuple[str, ...]
    expansion_refs: tuple[ExpansionRef, ...]
    diagnostics: PackDiagnostics

    def to_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)
