"""Enriquecimento semântico do grafo construído (lf-0027).

Camada independente entre ``GraphBuilder.build`` e ``persist_graph``:
re-resolve arestas não-resolvidas via ``ResolverCoordinator`` (ordem de
``EvidenceTier``: scip → lsp → …) e promove a aresta SOMENTE quando o
resultado é ``resolved`` e o alvo existe no snapshot atual.

Regras invioláveis:

- Uma aresta já ``resolved`` nunca é reescrita silenciosamente — só
  pode ser VERIFICADA por evidência de tier estritamente superior
  (lf-0035): mesmo alvo confirma e sobe a proveniência; alvo divergente
  vira ``ambiguous`` + diagnóstico de conflito, preservando o alvo
  original. Evidência externa nunca sobrescreve relação válida.
- Resultado não-``resolved`` (ambiguous/unresolved/external/diagnóstico)
  preserva a aresta original verbatim — nenhuma degradação.
- O ``target_id`` retornado por um resolver precisa ser nó persistível
  DESTE build — vínculo cross-snapshot é impossível por construção.
- Projeção documentada: a primeira evidência ``resolved`` no tier mais
  alto vence; demais respostas ficam como diagnósticos (coexistência
  de evidências alternativas fica registrada, não projetada).
- Exceções de resolver viram ``ParseDiagnostic`` — nunca crash.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping

from code_atlas.contracts.enums import NodeKind
from code_atlas.parsers.contracts import (
    EvidenceRecord,
    GraphBuildResult,
    GraphEdge,
    ParseDiagnostic,
)
from code_atlas.resolvers.capabilities import EvidenceTier
from code_atlas.resolvers.contracts import (
    ResolutionRequest,
    ResolutionResult,
)
from code_atlas.resolvers.coordinator import ResolverCoordinator

# Tipos de aresta elegíveis a enriquecimento → kind do ResolutionRequest.
_EDGE_TO_KIND = {
    "CALLS": "call",
    "IMPORTS": "import",
    "EXTENDS": "type-ref",
    "IMPLEMENTS": "type-ref",
    "REFERENCES": "reference",
}

# Kinds cujos nós viram ocorrências persistidas (espelha _PERSISTED_KINDS
# de indexing/persist — manter alinhado: um alvo fora deste conjunto
# seria descartado no persist, perdendo a aresta original).
_NON_PERSISTED = frozenset({NodeKind.REPOSITORY, NodeKind.PACKAGE})


def _edge_tier(resolver_label: str) -> int:
    """Tier da evidência que produziu a resolução atual (lf-0035).

    Labels persistidos: ``scip`` → índice semântico, ``lsp:*`` → LSP,
    qualquer outro (``syntactic-scope-v1``, ``tree-sitter-*``) →
    sintático. Desconhecido = sintático (conservador, nunca superestima).
    """
    if resolver_label == "scip" or resolver_label.startswith("resolver:scip"):
        return int(EvidenceTier.SEMANTIC_INDEX)
    if resolver_label.startswith("lsp:"):
        return int(EvidenceTier.LSP)
    return int(EvidenceTier.SYNTACTIC)


class SemanticEnricher:
    """Promove arestas não-resolvidas com evidência de resolvers opt-in."""

    def __init__(self, coordinator: ResolverCoordinator) -> None:
        self._coord = coordinator

    def enrich(
        self, build: GraphBuildResult
    ) -> tuple[GraphBuildResult, dict[str, int]]:
        """Devolve (build possivelmente enriquecido, stats).

        Stats: ``semantic_attempted``, ``semantic_enriched``,
        ``semantic_diagnostics``.
        """
        node_by_id = {n.node_id: n for n in build.nodes}
        persistable = {
            n.node_id for n in build.nodes
            if n.kind not in _NON_PERSISTED and n.path
        }
        evidence: list[EvidenceRecord] = list(build.evidence)
        diagnostics: list[ParseDiagnostic] = list(build.diagnostics)
        stats = {
            "semantic_attempted": 0,
            "semantic_enriched": 0,
            "semantic_verified": 0,
            "semantic_confirmed": 0,
            "semantic_conflicts": 0,
            "semantic_diagnostics": 0,
        }
        edges: list[GraphEdge] = []
        for e in build.edges:
            new_edge = self._enrich_edge(
                e, node_by_id, persistable, evidence, diagnostics, stats
            )
            edges.append(new_edge)
        stats["semantic_diagnostics"] = (
            len(diagnostics) - len(build.diagnostics)
        )
        enriched = dataclasses.replace(
            build,
            edges=tuple(edges),
            evidence=tuple(evidence),
            diagnostics=tuple(diagnostics),
        )
        return enriched, stats

    # -- por aresta ------------------------------------------------------

    def _enrich_edge(
        self,
        e: GraphEdge,
        node_by_id: Mapping[str, object],
        persistable: set[str],
        evidence: list[EvidenceRecord],
        diagnostics: list[ParseDiagnostic],
        stats: dict[str, int],
    ) -> GraphEdge:
        kind = _EDGE_TO_KIND.get(str(e.type))
        if kind is None:
            return e
        if e.resolution_status == "resolved":
            return self._verify_edge(
                e, kind, node_by_id, persistable, evidence,
                diagnostics, stats,
            )
        src = node_by_id.get(e.from_id)
        language = getattr(src, "language", "") if src is not None else ""
        request = ResolutionRequest(
            name=e.to_text,
            kind=kind,
            path=e.source_path,
            language=str(language),
            span=e.span,
        )
        if not request.name:
            return e
        stats["semantic_attempted"] += 1
        result = self._coord.resolve(request)
        self._diags(result, e, diagnostics)
        ref = result.reference
        if (
            ref is None
            or ref.status != "resolved"
            or ref.target_id is None
            or ref.target_id not in persistable
        ):
            return e
        prov = ref.provenance
        ev_idx = len(evidence)
        evidence.append(
            EvidenceRecord(
                path=e.source_path,
                span=e.span,
                content_hash="",
                extractor=f"resolver:{prov.resolver_id}",
                extractor_version=prov.resolver_version,
                notes=(
                    f"semantic enrichment tier={prov.evidence_tier} "
                    f"{prov.detail}".strip()
                ),
                resolver_id=prov.resolver_id,
                resolver_version=prov.resolver_version,
                evidence_tier=prov.evidence_tier,
                index_hash=prov.index_hash,
                validation_status="resolved",
            )
        )
        stats["semantic_enriched"] += 1
        return dataclasses.replace(
            e,
            to_id=ref.target_id,
            resolution_status="resolved",
            confidence_class=ref.confidence_class,
            resolver=prov.resolver_id,
            evidence_index=(*e.evidence_index, ev_idx),
        )

    def _verify_edge(
        self,
        e: GraphEdge,
        kind: str,
        node_by_id: Mapping[str, object],
        persistable: set[str],
        evidence: list[EvidenceRecord],
        diagnostics: list[ParseDiagnostic],
        stats: dict[str, int],
    ) -> GraphEdge:
        """Verifica aresta ``resolved`` contra tiers superiores (lf-0035).

        Determinístico:
        - Sem resolver acima do tier atual → aresta intacta.
        - Tier superior confirma o MESMO alvo → proveniência sobe.
        - Tier superior diverge (alvo persistível distinto) →
          ``ambiguous`` + evidência ``conflict`` + diagnóstico; o alvo
          original permanece (evidência externa nunca sobrescreve)."""
        current_tier = _edge_tier(e.resolver)
        if current_tier <= int(EvidenceTier.SEMANTIC_INDEX):
            return e  # nada acima do tier mais alto
        name = e.to_text
        if not name:
            tgt = node_by_id.get(e.to_id) if e.to_id else None
            name = getattr(tgt, "name", "") if tgt is not None else ""
        if not name:
            return e
        src = node_by_id.get(e.from_id)
        language = getattr(src, "language", "") if src is not None else ""
        request = ResolutionRequest(
            name=name, kind=kind, path=e.source_path,
            language=str(language), span=e.span,
        )
        above = [
            r for r in self._coord.candidates(request)
            if r.capabilities().tier < current_tier
        ]
        if not above:
            return e  # sem resolvedor superior configurado — custo zero
        stats["semantic_verified"] += 1
        result = self._coord.resolve(request, above_tier=current_tier)
        self._diags(result, e, diagnostics)
        ref = result.reference
        if ref is None or ref.status != "resolved" or ref.target_id is None:
            return e
        prov = ref.provenance
        if ref.target_id == e.to_id:
            ev_idx = len(evidence)
            evidence.append(
                EvidenceRecord(
                    path=e.source_path, span=e.span, content_hash="",
                    extractor=f"resolver:{prov.resolver_id}",
                    extractor_version=prov.resolver_version,
                    notes=(
                        f"semantic confirmed tier={prov.evidence_tier} "
                        f"{prov.detail}".strip()
                    ),
                    resolver_id=prov.resolver_id,
                    resolver_version=prov.resolver_version,
                    evidence_tier=prov.evidence_tier,
                    index_hash=prov.index_hash,
                    validation_status="confirmed",
                )
            )
            stats["semantic_confirmed"] += 1
            return dataclasses.replace(
                e,
                confidence_class=ref.confidence_class,
                resolver=prov.resolver_id,
                evidence_index=(*e.evidence_index, ev_idx),
            )
        if ref.target_id not in persistable:
            return e  # alvo alternativo não existe no snapshot — ignora
        ev_idx = len(evidence)
        evidence.append(
            EvidenceRecord(
                path=e.source_path, span=e.span, content_hash="",
                extractor=f"resolver:{prov.resolver_id}",
                extractor_version=prov.resolver_version,
                notes=(
                    f"semantic conflict tier={prov.evidence_tier}: "
                    f"kept={e.to_id} alternative={ref.target_id} "
                    f"{prov.detail}".strip()
                ),
                resolver_id=prov.resolver_id,
                resolver_version=prov.resolver_version,
                evidence_tier=prov.evidence_tier,
                index_hash=prov.index_hash,
                validation_status="conflict",
            )
        )
        diagnostics.append(
            ParseDiagnostic(
                code="semantic-conflict",
                severity="warning",
                message=(
                    f"{e.source_path}: {e.type} {e.from_id} -> {e.to_id}: "
                    f"resolver {prov.resolver_id} aponta {ref.target_id}"
                ),
                span=e.span,
            )
        )
        stats["semantic_conflicts"] += 1
        return dataclasses.replace(
            e,
            resolution_status="ambiguous",
            evidence_index=(*e.evidence_index, ev_idx),
        )

    @staticmethod
    def _diags(
        result: ResolutionResult,
        e: GraphEdge,
        out: list[ParseDiagnostic],
    ) -> None:
        """Traduz ResolutionDiagnostics do resolver para ParseDiagnostic
        — erros avisam; infos de `no-occurrence`/`no-resolver` só entram
        quando severity ≥ warning para não inundar o snapshot."""
        for d in result.diagnostics:
            if d.severity == "info":
                continue
            out.append(
                ParseDiagnostic(
                    code=f"semantic:{d.resolver_id}:{d.code}",
                    severity=d.severity,
                    message=d.message,
                    span=e.span,
                )
            )
