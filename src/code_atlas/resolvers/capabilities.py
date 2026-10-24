"""Capabilities e tiers de evidência do subsistema de resolução."""

from __future__ import annotations

import enum
from dataclasses import dataclass


class EvidenceTier(enum.IntEnum):
    """Ordem de precedência de evidência (prompt Cycle 3 Phase 1)."""

    SEMANTIC_INDEX = 1   # SCIP e afins — índice externo confiável
    LSP = 2              # servidor de linguagem confirmado
    SYNTACTIC = 3        # resolução sintática inequívoca
    HEURISTIC = 4        # candidato heurístico


@dataclass(frozen=True)
class ResolverCapability:
    """Declaração do que um resolvedor cobre."""

    resolver_id: str
    tier: EvidenceTier
    languages: frozenset[str]           # vazio = qualquer linguagem
    relation_types: frozenset[str]      # IMPORTS/CALLS/REFERENCES/...
    needs_external_process: bool = False
    needs_network: bool = False
    detail: str = ""

    def covers(self, language: str, relation_type: str) -> bool:
        lang_ok = not self.languages or language in self.languages
        rel_ok = not self.relation_types or relation_type in self.relation_types
        return lang_ok and rel_ok
