"""Consultas tipadas em memória sobre GraphBuildResult.

Camada leve usada pelo storage (lf-0011) e pela CLI futura — sem I/O.
"""

from __future__ import annotations

from code_atlas.parsers.contracts import GraphBuildResult, GraphEdge, GraphNode


class GraphView:
    """Visão indexada de um GraphBuildResult."""

    def __init__(self, result: GraphBuildResult) -> None:
        self.result = result
        self._by_id = {n.node_id: n for n in result.nodes}
        self._from: dict[str, list[GraphEdge]] = {}
        self._to: dict[str, list[GraphEdge]] = {}
        for e in result.edges:
            self._from.setdefault(e.from_id, []).append(e)
            if e.to_id is not None:
                self._to.setdefault(e.to_id, []).append(e)

    def node(self, node_id: str) -> GraphNode | None:
        return self._by_id.get(node_id)

    def by_kind(self, kind: str) -> list[GraphNode]:
        return [n for n in self.result.nodes if n.kind == kind]

    def by_name(self, name: str) -> list[GraphNode]:
        return [n for n in self.result.nodes if n.name == name]

    def find(self, qualified_name: str) -> list[GraphNode]:
        return [
            n for n in self.result.nodes if n.qualified_name == qualified_name
        ]

    def edges_from(self, node_id: str, type_: str | None = None) -> list[GraphEdge]:
        out = self._from.get(node_id, [])
        return [e for e in out if type_ is None or e.type == type_]

    def edges_to(self, node_id: str, type_: str | None = None) -> list[GraphEdge]:
        out = self._to.get(node_id, [])
        return [e for e in out if type_ is None or e.type == type_]

    def stats(self) -> dict[str, int]:
        return dict(self.result.stats)
