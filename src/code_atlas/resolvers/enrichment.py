"""Enriquecimento semântico do grafo construído (lf-0027).

Camada independente entre ``GraphBuilder.build`` e ``persist_graph``:
re-resolve arestas não-resolvidas via ``ResolverCoordinator`` (ordem de
``EvidenceTier``: scip → lsp → …) e promove a aresta SOMENTE quando o
resultado é ``resolved`` e o alvo existe no snapshot atual.

Regras invioláveis:

- Uma aresta já ``resolved`` nunca é reaberta nem reescrita.
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
        if kind is None or e.resolution_status == "resolved":
            return e
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
