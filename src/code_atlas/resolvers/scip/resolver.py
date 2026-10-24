"""ScipResolver — resolvedor EvidenceTier.SEMANTIC_INDEX (lf-0017).

Consome um ``ScipIndex`` já importado e validado e resolve
referências/definições via correlação explícita (``ScipCorrelator``) com
os nós do grafo Atlas. A proveniência carrega a ferramenta+versão do
indexer externo; ``confidence_class='resolved'`` só quando a identidade
do símbolo foi verificada (alvo único no snapshot).
"""

from __future__ import annotations

from code_atlas.parsers.contracts import SourceSpan
from code_atlas.resolvers.capabilities import EvidenceTier, ResolverCapability
from code_atlas.resolvers.contracts import (
    ResolutionDiagnostic,
    ResolutionRequest,
    ResolutionResult,
    ResolvedDefinition,
    ResolvedReference,
    ResolverProvenance,
)
from code_atlas.resolvers.scip.importer import (
    ROLE_DEFINITION,
    ScipDocument,
    ScipIndex,
    ScipOccurrence,
    ScipRange,
)
from code_atlas.resolvers.scip.mapper import ScipCorrelator, parse_symbol
from code_atlas.resolvers.syntactic.resolver import ScopeUniverse

RESOLVER_ID = "scip"
RESOLVER_VERSION = "1.0.0"


class ScipResolver:
    """Resolve pedidos usando ocorrências de um índice SCIP importado."""

    def __init__(
        self,
        index: ScipIndex,
        universe: ScopeUniverse,
    ) -> None:
        self._index = index
        self._corr = ScipCorrelator(universe)
        self._docs = {d.relative_path: d for d in index.documents}

    @property
    def resolver_id(self) -> str:
        return RESOLVER_ID

    @property
    def resolver_version(self) -> str:
        return RESOLVER_VERSION

    @property
    def index(self) -> ScipIndex:
        return self._index

    def capabilities(self) -> ResolverCapability:
        return ResolverCapability(
            resolver_id=RESOLVER_ID,
            tier=EvidenceTier.SEMANTIC_INDEX,
            languages=frozenset(),
            relation_types=frozenset(
                {"REFERENCES", "CALLS", "EXTENDS", "IMPLEMENTS", "IMPORTS"}
            ),
            detail=(
                f"scip index by {self._index.tool_name}"
                f" {self._index.tool_version}".strip()
            ),
        )

    def _prov(self) -> ResolverProvenance:
        return ResolverProvenance(
            resolver_id=RESOLVER_ID,
            resolver_version=RESOLVER_VERSION,
            evidence_tier=int(EvidenceTier.SEMANTIC_INDEX),
            detail=(
                f"{self._index.tool_name} {self._index.tool_version}".strip()
            ),
        )

    # -- contrato SemanticResolver --------------------------------------

    def resolve(self, request: ResolutionRequest) -> ResolutionResult:
        doc = self._docs.get(request.path)
        if doc is None:
            return ResolutionResult(
                request=request,
                diagnostics=(self._diag("document-not-in-index",
                                        f"no scip document for "
                                        f"{request.path}", "warning"),),
            )
        occ = self._occurrence_at(doc, request)
        if occ is None:
            return ResolutionResult(
                request=request,
                reference=ResolvedReference(
                    status="unresolved",
                    confidence_class="syntactic",
                    provenance=self._prov(),
                    target_text=request.name,
                ),
                diagnostics=(self._diag("no-occurrence",
                                        "no scip occurrence at span",
                                        "info"),),
            )
        return self._resolve_symbol(request, occ.symbol,
                                    is_definition=occ.is_definition)

    # -- resolução por símbolo scip --------------------------------------

    def resolve_symbol(self, request: ResolutionRequest,
                       scip_symbol: str) -> ResolutionResult:
        """API direta: resolve uma string de símbolo scip."""
        return self._resolve_symbol(request, scip_symbol)

    def _resolve_symbol(
        self,
        request: ResolutionRequest,
        scip_symbol: str,
        *,
        is_definition: bool = False,
    ) -> ResolutionResult:
        sym = parse_symbol(scip_symbol)
        if sym is None:
            return ResolutionResult(
                request=request,
                reference=ResolvedReference(
                    status="unresolved",
                    confidence_class="heuristic",
                    provenance=self._prov(),
                    target_text=scip_symbol,
                ),
                diagnostics=(self._diag("bad-symbol",
                                        f"unparseable scip symbol "
                                        f"{scip_symbol!r}", "warning"),),
            )
        path = request.path
        nid = self._corr.node_for_symbol(scip_symbol, path)
        if nid is not None:
            status = "resolved"
            conf = "resolved"  # identidade verificada — alvo único
        else:
            cands = self._corr.candidates_for_symbol(scip_symbol)
            if len(cands) > 1:
                return ResolutionResult(
                    request=request,
                    reference=ResolvedReference(
                        status="ambiguous",
                        confidence_class="syntactic",
                        provenance=self._prov(),
                        target_text=scip_symbol,
                        candidates=cands,
                    ),
                )
            # símbolo scip válido fora do snapshot → externo (pacote
            # de terceiro) ou não correlacionável
            status = "external" if not sym.is_local else "unresolved"
            conf = "syntactic"
        defs = self._definitions_for(scip_symbol) if is_definition else ()
        return ResolutionResult(
            request=request,
            reference=ResolvedReference(
                status=status,
                confidence_class=conf,
                provenance=self._prov(),
                target_id=nid,
                target_text=scip_symbol,
            ),
            definitions=defs,
        )

    def _definitions_for(self, scip_symbol: str) -> tuple[ResolvedDefinition, ...]:
        """Occurrences marcadas como Definition para o símbolo."""
        out: list[ResolvedDefinition] = []
        for d in self._index.documents:
            for occ in d.occurrences:
                if occ.symbol == scip_symbol and occ.roles & ROLE_DEFINITION:
                    out.append(
                        ResolvedDefinition(
                            symbol=scip_symbol,
                            path=d.relative_path,
                            provenance=self._prov(),
                            span=self._span_of(occ),
                        )
                    )
        return tuple(out)

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _span_of(occ: ScipOccurrence) -> SourceSpan | None:
        rng: ScipRange | None = occ.range
        if rng is None:
            return None
        # scip usa linhas 0-based; Atlas SourceSpan usa 1-based
        return SourceSpan(
            start_byte=0, end_byte=0,
            start_line=rng.start_line + 1, start_col=rng.start_char,
            end_line=rng.end_line + 1, end_col=rng.end_char,
        )

    @staticmethod
    def _occurrence_at(
        doc: ScipDocument, request: ResolutionRequest
    ) -> ScipOccurrence | None:
        """Seleciona a occurrence do documento que casa com o request.

        Preferência: occurrence cobrindo o span do request (convertido
        de linhas 1-based do Atlas para 0-based do scip); fallback
        heurístico por símbolo textual (nome no descriptor).
        """
        occs = doc.occurrences
        span = request.span
        if span is not None:
            s0 = (span.start_line - 1, span.start_col)
            e0 = (span.end_line - 1, span.end_col)
            for occ in occs:
                r = occ.range
                if r is None:
                    continue
                if (r.start_line, r.start_char) <= s0 and \
                        (r.end_line, r.end_char) >= e0:
                    return occ
            return None
        # sem span: casa pelo nome do request nos descriptors
        for occ in occs:
            sym = parse_symbol(occ.symbol)
            if sym is not None and sym.descriptors and \
                    sym.descriptors[-1].name == request.name:
                return occ
        return None

    def _diag(self, code: str, message: str, severity: str) -> ResolutionDiagnostic:
        return ResolutionDiagnostic(
            code=code, severity=severity, message=message,
            resolver_id=RESOLVER_ID,
        )
