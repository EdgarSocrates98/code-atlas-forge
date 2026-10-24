"""Grafo canônico de código (Cycle 2 Phase 4).

O builder é puro e determinístico: recebe ``ParseResult``s + inventário e
produz ``GraphBuildResult`` — persistência é responsabilidade do storage
(lf-0011). Nenhum caminho aqui toca SQLite ou tree_sitter.
"""

from code_atlas.graph.builder import GraphBuilder
from code_atlas.graph.identity import edge_key, node_id
from code_atlas.graph.queries import GraphView
from code_atlas.graph.validation import ValidationError, validate_graph

__all__ = [
    "GraphBuilder",
    "GraphView",
    "ValidationError",
    "edge_key",
    "node_id",
    "validate_graph",
]
