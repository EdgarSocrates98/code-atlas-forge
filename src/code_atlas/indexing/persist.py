"""Persistência do GraphBuildResult nas tabelas do Blueprint §6.

Projeção: nós com ``path`` real (file/module/symbol kinds) viram
``symbols`` + ``symbol_occurrences``; ``package``/``repository`` são
deriváveis dos paths e não ganham ocorrência. Arestas persistem quando
``from_id`` tem ocorrência mapeada — CONTAINS estruturais entre
repo/package/file são deriváveis via ``file_inventory`` + paths.

Decisão registrada (lf-0011): relações são entre ocorrências, conforme
``cross_snapshot_relations`` exige endpoints no mesmo snapshot.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from code_atlas.contracts.enums import NodeKind
from code_atlas.parsers.contracts import GraphBuildResult, SourceSpan
from code_atlas.storage.store import Store

# Kinds que ganham símbolo+ocorrência persistida.
_PERSISTED_KINDS = frozenset(
    {
        NodeKind.FILE, NodeKind.MODULE, NodeKind.CLASS, NodeKind.INTERFACE,
        NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.SYMBOL, NodeKind.CONFIG,
        NodeKind.UNKNOWN,
    }
)


def _span_json(span: SourceSpan | None) -> str | None:
    if span is None:
        return None
    return json.dumps(
        [
            span.start_byte, span.end_byte,
            span.start_line, span.start_col, span.end_line, span.end_col,
        ]
    )


def _persist_evidence(
    store: Store,
    snapshot_id: int,
    file_ids: Mapping[str, int],
    graph: GraphBuildResult,
) -> dict[int, int]:
    """Evidências → rows; retorna mapa índice-em-graph.evidence → row_id.

    Mapa explícito (lf-0015): evidências filtradas (path fora do
    inventário) NÃO podem deslocar os índices das demais — o modelo
    posicional compactado corrompia silenciosamente a associação
    evidence↔relation.
    """
    evi_row_ids: dict[int, int] = {}
    for i, rec in enumerate(graph.evidence):
        fid = file_ids.get(rec.path)
        if fid is None:
            continue
        evi_row_ids[i] = store.insert_evidence(
            snapshot_id, fid, rec.path, _span_json(rec.span),
            rec.content_hash, rec.extractor, rec.extractor_version,
            rec.notes,
        )
    return evi_row_ids


def persist_graph(
    store: Store,
    repo_id: int,
    snapshot_id: int,
    file_ids: Mapping[str, int],
    graph: GraphBuildResult,
    *,
    fts5: bool,
    file_status: Mapping[str, str] | None = None,
    external_occ: Mapping[str, int] | None = None,
) -> dict[str, int]:
    """Grava evidências, símbolos, ocorrências e relações — na transação
    corrente do caller (atomicidade é de quem chama).

    ``file_status`` mapeia path → parse_status da ocorrência
    (``parsed`` | ``parsed-with-errors`` | ``parse-failed``).

    ``external_occ`` (lf-0019): node_id → occ_id de ocorrências já
    copiadas de arquivos reutilizados — permite que arestas de arquivos
    reparseados amarrem alvos em módulos que não entraram no grafo.
    """
    counts = {
        "evidence": 0, "symbols_new": 0, "occurrences": 0,
        "relations": 0, "edges_skipped": 0,
    }
    status = file_status or {}

    # 1. evidências primeiro — relations referenciam ids de evidência.
    evi_row_ids = _persist_evidence(store, snapshot_id, file_ids, graph)
    counts["evidence"] = len(evi_row_ids)

    # 2. nós → símbolo + ocorrência (id do nó → id da ocorrência)
    node_occ: dict[str, int] = {}
    for n in graph.nodes:
        if n.kind not in _PERSISTED_KINDS or n.path not in file_ids:
            continue
        symbol_id, created = store.upsert_symbol(
            repo_id, n.language, n.qualified_name, str(n.kind)
        )
        if created:
            counts["symbols_new"] += 1
            if fts5:
                store.insert_fts_symbol(
                    symbol_id, n.qualified_name, n.language, str(n.kind)
                )
        span = n.span
        occ_id = store.insert_occurrence(
            snapshot_id, symbol_id, file_ids[n.path],
            span.start_byte if span else None,
            span.end_byte if span else None,
            span.start_line if span else None,
            span.start_col if span else None,
            n.content_hash,
            parse_status=status.get(n.path, "parsed"),
        )
        node_occ[n.node_id] = occ_id
        counts["occurrences"] += 1

    # 3. relações — somente endpoints dentro do snapshot
    rel_rows: list[tuple[object, ...]] = []
    for e in graph.edges:
        frm = node_occ.get(e.from_id)
        if frm is None:
            counts["edges_skipped"] += 1
            continue
        to = node_occ.get(e.to_id) if e.to_id is not None else None
        if to is None and e.to_id is not None and external_occ:
            to = external_occ.get(e.to_id)
        if e.to_id is not None and to is None:
            counts["edges_skipped"] += 1  # alvo estrutural não persistido
            continue
        ev_ids = json.dumps([evi_row_ids[i] for i in e.evidence_index
                             if i in evi_row_ids])
        rel_rows.append(
            (
                snapshot_id, frm, to, e.to_text or None, str(e.type),
                e.resolution_status, e.confidence_class, e.resolver, ev_ids,
            )
        )
    store.insert_relations(rel_rows)
    counts["relations"] = len(rel_rows)
    return counts
