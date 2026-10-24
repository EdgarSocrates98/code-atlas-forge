"""Validação estrutural do grafo (Gate 4).

Checagens: integridade referencial (endpoints existem), unicidade de IDs,
vocabulário de enums, índices de evidência válidos e ausência de ciclos
em CONTAINS. Não há snapshot aqui — isolamento entre snapshots é
garantido por constraint no storage (lf-0011) e auditado no coordinator.
"""

from __future__ import annotations

from code_atlas.contracts.enums import EdgeKind, NodeKind
from code_atlas.parsers.contracts import GraphBuildResult, GraphEdge


class ValidationError(Exception):
    """Violação de integridade do grafo."""


_RESOLUTION_STATUSES = frozenset(
    {"resolved", "ambiguous", "unresolved", "external"}
)
_CONFIDENCE_CLASSES = frozenset(
    {"resolved", "syntactic", "heuristic", "inferred"}
)


def validate_graph(result: GraphBuildResult) -> list[str]:
    """Retorna lista de violações (vazia = íntegro)."""
    errors: list[str] = []
    errors.extend(_check_nodes(result))
    node_ids = {n.node_id for n in result.nodes}
    for e in result.edges:
        _check_edge(e, node_ids, len(result.evidence), errors)
    errors.extend(_contains_cycles(result))
    return errors


def _check_nodes(result: GraphBuildResult) -> list[str]:
    errors: list[str] = []
    node_ids = {n.node_id for n in result.nodes}
    if len(node_ids) != len(result.nodes):
        errors.append("duplicate node ids")
    valid = set(NodeKind)
    for n in result.nodes:
        if n.kind not in valid:
            errors.append(f"invalid node kind: {n.kind}")
        if not n.node_id:
            errors.append("empty node_id")
    return errors


def _check_edge(
    e: GraphEdge, node_ids: set[str], evi_len: int, errors: list[str]
) -> None:
    if e.type not in set(EdgeKind):
        errors.append(f"invalid edge type: {e.type}")
    if e.from_id not in node_ids:
        errors.append(f"dangling from_id: {e.from_id}")
    if e.to_id is not None and e.to_id not in node_ids:
        errors.append(f"dangling to_id: {e.to_id}")
    if e.to_id is None and not e.to_text:
        errors.append(f"edge without target text: {e.type} from {e.from_id}")
    if e.resolution_status not in _RESOLUTION_STATUSES:
        errors.append(f"invalid resolution_status: {e.resolution_status}")
    if e.confidence_class not in _CONFIDENCE_CLASSES:
        errors.append(f"invalid confidence_class: {e.confidence_class}")
    for i in e.evidence_index:
        if not 0 <= i < evi_len:
            errors.append(f"evidence index out of range: {i}")


def _contains_cycles(result: GraphBuildResult) -> list[str]:
    """CONTAINS deve ser uma árvore (DAG); ciclo = violação."""
    child_of: dict[str, str] = {}
    errors: list[str] = []
    for e in result.edges:
        if e.type != EdgeKind.CONTAINS or e.to_id is None:
            continue
        if e.to_id in child_of:
            errors.append(f"CONTAINS multi-parent: {e.to_id}")
            continue
        child_of[e.to_id] = e.from_id
    for start in child_of:
        seen = set()
        cur = start
        while cur in child_of:
            if cur in seen:
                errors.append(f"CONTAINS cycle at {start}")
                break
            seen.add(cur)
            cur = child_of[cur]
    return errors
