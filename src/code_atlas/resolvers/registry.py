"""Registro de resolvedores (resolver_id → SemanticResolver)."""

from __future__ import annotations

from code_atlas.resolvers.contracts import SemanticResolver


class ResolverRegistry:
    """Registro explícito — nenhum resolvedor é ativado por padrão.

    Resolvers opt-in (SCIP/LSP) são registrados pelo caller; o registro
    nunca os constrói sozinho a partir do conteúdo do repositório.
    """

    def __init__(self) -> None:
        self._resolvers: dict[str, SemanticResolver] = {}

    def register(self, resolver: SemanticResolver) -> None:
        self._resolvers[resolver.resolver_id] = resolver

    def get(self, resolver_id: str) -> SemanticResolver | None:
        return self._resolvers.get(resolver_id)

    def resolvers(self) -> tuple[SemanticResolver, ...]:
        return tuple(self._resolvers.values())

    def capable(self, language: str, relation_type: str) -> list[SemanticResolver]:
        """Resolvers que cobrem a linguagem+relação, ordenados por tier."""
        ok = [
            r for r in self._resolvers.values()
            if r.capabilities().covers(language, relation_type)
        ]
        return sorted(ok, key=lambda r: r.capabilities().tier)
