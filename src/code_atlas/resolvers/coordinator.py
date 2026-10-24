"""Coordenador de resolução — cadeia por precedência de evidência.

Política (documentada): itera resolvedores em ordem de ``EvidenceTier``
crescente (índice semântico → LSP → sintático → heurístico). Para no
primeiro resultado ``resolved`` inequívoco — tiers mais baixos não
aprimoram um resultado já confirmado. Se nenhum resolver devolve
``resolved``, o resultado do **tier mais alto disponível** é devolvido
com seu status honesto (ambiguous/unresolved/external) — nunca promovido.
Exceções de resolvedor viram diagnóstico, não crash.
"""

from __future__ import annotations

from code_atlas.resolvers.contracts import (
    ResolutionDiagnostic,
    ResolutionRequest,
    ResolutionResult,
    SemanticResolver,
    relation_for_kind,
)
from code_atlas.resolvers.registry import ResolverRegistry


class ResolverCoordinator:
    """Encadeia resolvers registrados por ordem de evidência."""

    def __init__(self, registry: ResolverRegistry) -> None:
        self._registry = registry

    def candidates(
        self, request: ResolutionRequest
    ) -> list[SemanticResolver]:
        """Resolvers que cobrem o pedido, em ordem de evidência."""
        rel = relation_for_kind(request.kind)
        return self._registry.capable(request.language, rel)

    def resolve(
        self,
        request: ResolutionRequest,
        *,
        above_tier: int | None = None,
    ) -> ResolutionResult:
        """Resolve seguindo a cadeia de evidência; nunca levanta.

        ``above_tier`` (lf-0035): quando informado, só consulta
        resolvedores com ``tier < above_tier`` — usado para verificar
        uma resolução existente contra evidência estritamente superior
        (política de precedência determinística)."""
        diagnostics: list[ResolutionDiagnostic] = []
        best: ResolutionResult | None = None
        for resolver in self.candidates(request):
            if (
                above_tier is not None
                and resolver.capabilities().tier >= above_tier
            ):
                continue
            try:
                result = resolver.resolve(request)
            except Exception as exc:  # resolvedor isolado → diagnóstico
                diagnostics.append(
                    ResolutionDiagnostic(
                        code="resolver-error",
                        severity="warning",
                        message=f"{resolver.resolver_id}: {exc}",
                        resolver_id=resolver.resolver_id,
                    )
                )
                continue
            diagnostics.extend(result.diagnostics)
            ref = result.reference
            if ref is not None and ref.status == "resolved":
                return ResolutionResult(
                    request=request,
                    reference=ref,
                    diagnostics=tuple(diagnostics),
                )
            if best is None:
                best = result  # tier mais alto que respondeu
        if best is not None:
            return ResolutionResult(
                request=request,
                reference=best.reference,
                definitions=best.definitions,
                diagnostics=tuple(diagnostics),
            )
        return ResolutionResult(
            request=request,
            diagnostics=tuple(diagnostics) or (
                ResolutionDiagnostic(
                    code="no-resolver",
                    severity="warning",
                    message=f"no resolver covers '{request.kind}' "
                            f"for '{request.language or '*'}'",
                ),
            ),
        )
