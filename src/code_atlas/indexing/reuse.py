"""Reuso de dados do snapshot anterior no índice incremental (lf-0019).

Duas responsabilidades:

1. ``reconstruct_universe`` — reconstrói ``ModuleCtx``s de arquivos
   ``reuse`` a partir das linhas persistidas (``snapshot_file_symbols``),
   para que o ``GraphBuilder`` resolva relações de arquivos reparseados
   contra módulos não-reparseados. ``import_targets`` do arquivo
   reutilizado não é necessário: ele só serve para resolver as refs do
   próprio arquivo, que não são reavaliadas.

2. ``copy_*`` — copy-forward transacional de ``symbol_occurrences``,
   ``evidence`` e ``relations`` das fontes ``reuse`` para o snapshot
   novo, preservando spans/status/hash originais (a tabela de
   ocorrências é lossy: 4 campos de span, então copiar a linha é a
   única forma de manter fidelidade).

Cache real de ``ParseResult``/build transacional refinado é lf-0020 —
aqui o mínimo correto: copiar linhas e remapear FKs.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping

from code_atlas.graph import identity
from code_atlas.resolvers.syntactic.resolver import ModuleCtx, ScopeUniverse
from code_atlas.storage.store import Store

# kinds de símbolo que NÃO se copiam: ``file`` ganha ocorrência fresca a
# cada build (persist_graph emite nós de arquivo para todo o manifesto).
_SKIP_COPY_KINDS = frozenset({"file"})


def reconstruct_universe(
    store: Store,
    snapshot_id: int,
    paths: set[str],
) -> ScopeUniverse:
    """Reconstrói ctxs de módulo dos paths ``reuse`` a partir do banco.

    ``snapshot_file_symbols`` traz (path, kind, qname, language, fid);
    nids são determinísticos (``identity.*``), então o universo
    reconstruído é idêntico ao que o parse original produziu — exceto
    ``import_targets``, desnecessário para resolução alheia.
    """
    universe = ScopeUniverse()
    rows = store.snapshot_file_symbols(snapshot_id)
    by_file: dict[str, list[tuple[str, str, str]]] = {}
    for path, kind, qname, lang, _fid in rows:
        if path in paths:
            by_file.setdefault(path, []).append((kind, qname, lang))

    for path in sorted(by_file):
        entries = by_file[path]
        mod_qname = ""
        lang = ""
        for kind, qname, lg in entries:
            if kind == "module":
                mod_qname, lang = qname, lg
                break
        if not mod_qname:
            continue  # sem módulo persistido → caller re-parseia
        mid = identity.module_node_id(lang, path)
        ctx = ModuleCtx(path=path, module_name=mod_qname, language=lang,
                        nid=mid)
        universe.modules[mod_qname] = ctx
        universe.by_path[path] = mid
        universe.nid_mod[mid] = ctx
        universe.leaf.setdefault(mod_qname.split(".")[-1], []).append(mid)
        universe.qname_nid.setdefault(mod_qname, []).append(mid)
        for kind, qname, lg in entries:
            if kind == "module":
                continue
            nid = identity.symbol_node_id(lg, kind, qname)
            leaf_name = qname.rsplit(".", 1)[-1]
            ctx.defines.setdefault(leaf_name, []).append(nid)
            universe.leaf.setdefault(leaf_name, []).append(nid)
            universe.qname_nid.setdefault(qname, []).append(nid)
    return universe


def copy_occurrences_and_evidence(
    store: Store,
    prior_snapshot_id: int,
    snapshot_id: int,
    reused_file_ids: set[int],
) -> tuple[dict[str, int], dict[int, int], dict[int, int]]:
    """Copia ocorrências (exceto kind ``file``) e evidências dos arquivos
    reutilizados para o snapshot novo.

    Retorna ``(occ_by_nid, occ_map, evi_map)``:
    - ``occ_by_nid``: node_id determinístico → occ_id novo — usado por
      ``persist_graph`` para amarrar arestas de arquivos parseados a
      módulos reutilizados;
    - ``occ_map``/``evi_map``: id antigo → id novo (remap de relations).
    """
    conn: sqlite3.Connection = store.conn
    occ_map: dict[int, int] = {}
    occ_by_nid: dict[str, int] = {}
    evi_map: dict[int, int] = {}

    occ_rows = conn.execute(
        """
        SELECT o.id, o.logical_symbol_id, o.source_file_id, o.span_start,
               o.span_end, o.line, o.col, o.content_hash, o.parse_status,
               s.kind, s.qualified_name, s.language, sf.relative_path
          FROM symbol_occurrences o
          JOIN symbols s ON s.id = o.logical_symbol_id
          JOIN source_files sf ON sf.id = o.source_file_id
         WHERE o.snapshot_id = ?
        """,
        (prior_snapshot_id,),
    ).fetchall()
    for r in occ_rows:
        if r["source_file_id"] not in reused_file_ids:
            continue
        if r["kind"] in _SKIP_COPY_KINDS:
            continue
        new_id = store.insert_occurrence(
            snapshot_id, r["logical_symbol_id"], r["source_file_id"],
            r["span_start"], r["span_end"], r["line"], r["col"],
            r["content_hash"], parse_status=r["parse_status"],
        )
        occ_map[int(r["id"])] = new_id
        if r["kind"] == "module":
            nid = identity.module_node_id(r["language"], r["relative_path"])
        else:
            nid = identity.symbol_node_id(
                r["language"], r["kind"], r["qualified_name"]
            )
        occ_by_nid[nid] = new_id

    evi_rows = conn.execute(
        "SELECT id, source_file_id, path, span, content_hash, extractor,"
        " extractor_version, notes FROM evidence WHERE snapshot_id = ?",
        (prior_snapshot_id,),
    ).fetchall()
    for r in evi_rows:
        if r["source_file_id"] not in reused_file_ids:
            continue
        new_id = store.insert_evidence(
            snapshot_id, r["source_file_id"], r["path"], r["span"],
            r["content_hash"], r["extractor"], r["extractor_version"],
            r["notes"],
        )
        evi_map[int(r["id"])] = new_id

    return occ_by_nid, occ_map, evi_map


def copy_relations(
    store: Store,
    prior_snapshot_id: int,
    snapshot_id: int,
    reused_file_ids: set[int],
    occ_map: Mapping[int, int],
    evi_map: Mapping[int, int],
) -> dict[str, int]:
    """Copia relações originadas em arquivos reutilizados.

    ``from_id`` remapeia via ``occ_map``; ``to_id`` remapeia para a
    ocorrência do **mesmo símbolo lógico no mesmo arquivo** no snapshot
    novo (o alvo pode estar num arquivo reparseado — símbolo idêntico
    porque conteúdo idêntico — ou noutro reutilizado). Alvo ausente no
    snapshot novo → aresta descartada e contada (o planner deveria ter
    revalidado a origem; o contador torna o desvio observável).
    """
    conn: sqlite3.Connection = store.conn
    rows = conn.execute(
        """
        SELECT r.id, r.from_id, r.to_id, r.to_unresolved, r.type,
               r.resolution_status, r.confidence_class, r.resolver,
               r.evidence_ids, o_from.source_file_id AS from_fid,
               o_from.logical_symbol_id AS from_sym,
               o_to.logical_symbol_id AS to_sym, o_to.source_file_id AS to_fid
          FROM relations r
          JOIN symbol_occurrences o_from ON o_from.id = r.from_id
          LEFT JOIN symbol_occurrences o_to ON o_to.id = r.to_id
         WHERE r.snapshot_id = ?
         ORDER BY r.id
        """,
        (prior_snapshot_id,),
    ).fetchall()

    def _new_occ(old_id: int, sym: int, fid: int) -> int | None:
        """Remapeia endpoint: via occ_map (copiados) ou lookup por
        (símbolo lógico, arquivo) — cobre nós ``file`` que ganharam
        ocorrência fresca e alvos em arquivos reparseados."""
        cached = occ_map.get(old_id)
        if cached is not None:
            return cached
        found = conn.execute(
            "SELECT id FROM symbol_occurrences WHERE snapshot_id = ?"
            " AND logical_symbol_id = ? AND source_file_id = ?",
            (snapshot_id, sym, fid),
        ).fetchone()
        return int(found["id"]) if found else None

    copied = dropped = 0
    rel_rows: list[tuple[object, ...]] = []
    for r in rows:
        if r["from_fid"] not in reused_file_ids:
            continue
        new_from = _new_occ(int(r["from_id"]), int(r["from_sym"]),
                            int(r["from_fid"]))
        if new_from is None:
            dropped += 1
            continue
        new_to: int | None = None
        if r["to_id"] is not None:
            new_to = _new_occ(int(r["to_id"]), int(r["to_sym"]),
                              int(r["to_fid"]))
            if new_to is None:
                dropped += 1
                continue
        ev_ids = json.dumps(
            [
                evi_map[i]
                for i in json.loads(r["evidence_ids"])
                if i in evi_map
            ]
        )
        rel_rows.append(
            (
                snapshot_id, new_from, new_to, r["to_unresolved"],
                r["type"], r["resolution_status"], r["confidence_class"],
                r["resolver"], ev_ids,
            )
        )
        copied += 1
    store.insert_relations(rel_rows)
    return {"relations_copied": copied, "relations_dropped": dropped}
