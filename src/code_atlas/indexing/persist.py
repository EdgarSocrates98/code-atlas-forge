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
from code_atlas.search.tokenizer import index_terms
from code_atlas.storage.store import Store

# Docstring truncada no índice FTS — não copiamos o repo integral.
_DOCSTRING_FTS_MAX = 4000

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
    rows: list[tuple[object, ...]] = []
    order: list[int] = []  # índice em graph.evidence alinhado a `rows`
    for i, rec in enumerate(graph.evidence):
        fid = file_ids.get(rec.path)
        if fid is None:
            continue
        rows.append(
            (snapshot_id, fid, rec.path, _span_json(rec.span),
             rec.content_hash, rec.extractor, rec.extractor_version,
             rec.notes, rec.resolver_id, rec.resolver_version,
             rec.evidence_tier, rec.index_hash, rec.validation_status)
        )
        order.append(i)
    for idx, rid in zip(order, store.insert_evidence_batch(rows), strict=True):
        evi_row_ids[idx] = rid
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
) -> dict[str, int]:
    """Grava evidências, símbolos, ocorrências e relações — na transação
    corrente do caller (atomicidade é de quem chama).

    ``file_status`` mapeia path → parse_status da ocorrência
    (``parsed`` | ``parsed-with-errors`` | ``parse-failed``).
    """
    counts = {
        "evidence": 0, "symbols_new": 0, "occurrences": 0,
        "relations": 0, "edges_skipped": 0,
    }
    status = file_status or {}

    # 1. evidências primeiro — relations referenciam ids de evidência.
    evi_row_ids = _persist_evidence(store, snapshot_id, file_ids, graph)
    counts["evidence"] = len(evi_row_ids)

    # 2. nós → símbolo + ocorrência (id do nó → id da ocorrência).
    # lf-0023: dedup em duas fases + multi-row INSERT…RETURNING — o
    # per-row upsert/insert era o custo dominante (~145k round-trips
    # num corpus de 5k arquivos).
    persisted = [
        n for n in graph.nodes
        if n.kind in _PERSISTED_KINDS and n.path in file_ids
    ]
    sym_ids, created = store.ensure_symbols(
        repo_id,
        [(n.language, n.qualified_name, str(n.kind)) for n in persisted],
    )
    counts["symbols_new"] = len(created)
    if fts5 and created:
        store.insert_symbols_batch(
            (sym_ids[k], k[1], k[0], k[2]) for k in sorted(created)
        )
    node_occ: dict[str, int] = {}
    occ_rows: list[tuple[object, ...]] = []
    occ_nodes: list[str] = []  # node_id alinhado a occ_rows
    for n in persisted:
        span = n.span
        occ_rows.append(
            (
                snapshot_id, sym_ids[(n.language, n.qualified_name, str(n.kind))],
                file_ids[n.path],
                span.start_byte if span else None,
                span.end_byte if span else None,
                span.start_line if span else None,
                span.start_col if span else None,
                n.content_hash,
                status.get(n.path, "parsed"),
            )
        )
        occ_nodes.append(n.node_id)
    for nid, rid in zip(
        occ_nodes, store.insert_occurrences_batch(occ_rows), strict=True
    ):
        node_occ[nid] = rid
    counts["occurrences"] = len(node_occ)

    # 2b. FTS por-ocorrência (lf-0036) — snapshot-scoped; rowid = id da
    # ocorrência. ``terms`` recebe identificadores pré-tokenizados;
    # docstring truncada (não copiamos o repo integral).
    if fts5 and node_occ:
        node_by_id = {n.node_id: n for n in persisted}
        fts_rows = [
            (
                occ_id,
                snapshot_id,
                (n := node_by_id[nid]).name
                or n.qualified_name.rsplit(".", 1)[-1],
                index_terms(
                    n.name, n.qualified_name, n.path
                ),
                n.signature or "",
                (n.docstring or "")[:_DOCSTRING_FTS_MAX],
                n.path,
                n.language,
                str(n.kind),
            )
            for nid, occ_id in node_occ.items()
        ]
        store.insert_fts_occurrences_batch(fts_rows)

    # 3. relações — somente endpoints dentro do snapshot
    rel_rows: list[tuple[object, ...]] = []
    for e in graph.edges:
        frm = node_occ.get(e.from_id)
        if frm is None:
            counts["edges_skipped"] += 1
            continue
        to = node_occ.get(e.to_id) if e.to_id is not None else None
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
