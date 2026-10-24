"""Identidade determinística de nós e arestas.

Regra inegociável (Phase 3/4): nunca usar número de linha como
identidade. IDs são derivados de entradas estáveis — kind, path,
qualified_name — o que torna o grafo reproduzível entre processos.
"""

from __future__ import annotations

import hashlib

from code_atlas.parsers.contracts import GraphEdge, SourceSpan


def node_id(kind: str, *parts: str) -> str:
    """ID legível e determinístico: ``kind:part1:part2``.

    Ex.: ``file:pkg/a.py``, ``sym:python:function:pkg.a.f``.
    """
    return ":".join((kind, *parts))


def symbol_node_id(language: str, kind: str, qualified_name: str) -> str:
    return node_id("sym", language, kind, qualified_name)


def file_node_id(path: str) -> str:
    return node_id("file", path)


def module_node_id(language: str, path: str) -> str:
    return node_id("module", language, path)


def package_node_id(dir_path: str) -> str:
    return node_id("package", dir_path)


def repo_node_id(name: str) -> str:
    return node_id("repository", name)


def edge_key(edge: GraphEdge, span: SourceSpan | None) -> str:
    """Chave determinística da aresta (dedup e ordenação)."""
    raw = "|".join(
        (
            edge.type,
            edge.from_id,
            edge.to_id or "",
            edge.to_text,
            "" if span is None else f"{span.start_byte}:{span.end_byte}",
        )
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:24]
