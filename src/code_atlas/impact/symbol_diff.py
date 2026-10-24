"""Diff semântico de símbolos entre snapshots (Phase 3).

Identidade estável = ``language:kind:qualified_name`` (mesma forma do
canonical_key do hybrid). Rename/move só quando há evidência:
mesmo ``content_hash`` de ocorrência → ``supported``; sem evidência,
add/remove separados — nunca "parecido" vira rename.

Cobertura: arquivos sem parser não produzem símbolos — entram em
``unknown_coverage`` do resultado, não como símbolos inventados.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from code_atlas.impact.contracts import (
    EvidenceStrength,
    SymbolChange,
    SymbolChangeKind,
)

_OCC_SQL = (
    "SELECT o.id, o.source_file_id, o.content_hash, f.relative_path,"
    " s.qualified_name, s.language, s.kind"
    " FROM symbol_occurrences o"
    " JOIN source_files f ON f.id = o.source_file_id"
    " JOIN symbols s ON s.id = o.logical_symbol_id"
    " WHERE o.snapshot_id = ?"
)

_SIG_SQL = (
    "SELECT signature FROM fts_occurrences WHERE rowid = ?"
)


@dataclass(frozen=True)
class _Sym:
    key: str
    qname: str
    language: str
    kind: str
    occ_id: int
    path: str
    content_hash: str | None


def _symbols(conn: sqlite3.Connection, snapshot_id: int) -> dict[str, _Sym]:
    """Símbolo lógico → ocorrência representativa (a de menor id)."""
    conn.row_factory = sqlite3.Row
    out: dict[str, _Sym] = {}
    for r in conn.execute(_OCC_SQL, (snapshot_id,)):
        key = f"{r['language']}:{r['kind']}:{r['qualified_name']}"
        prev = out.get(key)
        if prev is None or int(r["id"]) < prev.occ_id:
            out[key] = _Sym(
                key=key, qname=str(r["qualified_name"]),
                language=str(r["language"]), kind=str(r["kind"]),
                occ_id=int(r["id"]), path=str(r["relative_path"]),
                content_hash=r["content_hash"])
    return out


def _pair_by_hash(
    removed: dict[str, _Sym], added: dict[str, _Sym],
) -> tuple[dict[str, list[_Sym]], dict[str, list[_Sym]],
           dict[str, _Sym], set[str]]:
    """Agrupa por content_hash e extrai pares 1:1 (lf-0073).

    Retorna (by_hash_removed, by_hash_added, rename_pairs, ambiguous);
    os pares 1:1 são retirados de `removed`/`added` in-place.
    O primeiro elemento só serve para documentar o agrupamento.
    """
    by_hash_removed: dict[str, list[_Sym]] = {}
    for s in removed.values():
        if s.content_hash:
            by_hash_removed.setdefault(s.content_hash, []).append(s)
    by_hash_added: dict[str, list[_Sym]] = {}
    for s in added.values():
        if s.content_hash:
            by_hash_added.setdefault(s.content_hash, []).append(s)
    ambiguous: set[str] = set()
    rename_pairs: dict[str, _Sym] = {}
    for h, olds in by_hash_removed.items():
        news = by_hash_added.get(h, [])
        if len(olds) == 1 and len(news) == 1:
            old, new = olds[0], news[0]
            rename_pairs[new.key] = old
            removed.pop(old.key)
            added.pop(new.key)
        elif news:
            ambiguous.add(h)
    return by_hash_removed, by_hash_added, rename_pairs, ambiguous


def _signature(conn: sqlite3.Connection, occ_id: int) -> str | None:
    row = conn.execute(_SIG_SQL, (occ_id,)).fetchone()
    return str(row["signature"]) if row and row["signature"] else None


def diff_snapshots(
    conn: sqlite3.Connection, base_snapshot: int, target_snapshot: int,
    *,
    max_changes: int = 500,
) -> tuple[tuple[SymbolChange, ...], tuple[str, ...], bool]:
    """(changes, unknown_coverage, truncated) entre dois snapshots.

    Determinístico: ordenação por symbol_key.
    """
    base = _symbols(conn, base_snapshot)
    target = _symbols(conn, target_snapshot)
    changes: list[SymbolChange] = []
    unknown: list[str] = []

    removed = {k: v for k, v in base.items() if k not in target}
    added = {k: v for k, v in target.items() if k not in base}

    # rename/move com evidência: mesmo content_hash em qname diferente.
    # lf-0073: hash só prova 1:1 — N candidatos em qualquer lado torna
    # a correspondência AMBÍGUA (add/remove separados + detail), nunca
    # um rename "verified".
    by_hash_added, rename_pairs, ambiguous = _pair_by_hash(
        removed, added)[1:]

    for k, s in sorted(removed.items()):
        if s.content_hash in ambiguous:
            cand = by_hash_added.get(s.content_hash or "", [])
            cands = ", ".join(c.qname for c in cand)
            changes.append(SymbolChange(
                symbol_key=k, qualified_name=s.qname,
                language=s.language, kind=s.kind,
                change=SymbolChangeKind.REMOVED,
                strength=EvidenceStrength.POSSIBLE,
                old_signature=_signature(conn, s.occ_id),
                old_path=s.path, old_occurrence_id=s.occ_id,
                detail=(f"ambíguo: {len(cand)} candidato(s) com mesmo "
                        f"hash ({cands}) — correspondência não provada")))
            continue
        changes.append(SymbolChange(
            symbol_key=k, qualified_name=s.qname, language=s.language,
            kind=s.kind, change=SymbolChangeKind.REMOVED,
            strength=EvidenceStrength.VERIFIED,
            old_signature=_signature(conn, s.occ_id),
            old_path=s.path, old_occurrence_id=s.occ_id))
    for k, s in sorted(added.items()):
        amb = s.content_hash in ambiguous
        changes.append(SymbolChange(
            symbol_key=k, qualified_name=s.qname, language=s.language,
            kind=s.kind, change=SymbolChangeKind.ADDED,
            strength=(EvidenceStrength.POSSIBLE if amb
                      else EvidenceStrength.VERIFIED),
            new_signature=_signature(conn, s.occ_id),
            new_path=s.path, new_occurrence_id=s.occ_id,
            detail=("ambíguo: mesmo hash de símbolo removido"
                    if amb else "")))

    for new_key, old in sorted(rename_pairs.items()):
        new = target[new_key]
        change = (SymbolChangeKind.RENAMED if old.qname != new.qname
                  else SymbolChangeKind.MOVED)
        changes.append(SymbolChange(
            symbol_key=new_key, qualified_name=new.qname,
            language=new.language, kind=new.kind, change=change,
            strength=EvidenceStrength.SUPPORTED,
            old_signature=_signature(conn, old.occ_id),
            new_signature=_signature(conn, new.occ_id),
            old_path=old.path, new_path=new.path,
            old_occurrence_id=old.occ_id,
            new_occurrence_id=new.occ_id,
            detail="match por content_hash"))

    for k in sorted(set(base) & set(target)):
        old, new = base[k], target[k]
        old_sig = _signature(conn, old.occ_id)
        new_sig = _signature(conn, new.occ_id)
        if old.path != new.path:
            changes.append(SymbolChange(
                symbol_key=k, qualified_name=new.qname,
                language=new.language, kind=new.kind,
                change=SymbolChangeKind.MOVED,
                strength=EvidenceStrength.VERIFIED,
                old_signature=old_sig, new_signature=new_sig,
                old_path=old.path, new_path=new.path,
                old_occurrence_id=old.occ_id,
                new_occurrence_id=new.occ_id))
        elif (old_sig or new_sig) and old_sig != new_sig:
            changes.append(SymbolChange(
                symbol_key=k, qualified_name=new.qname,
                language=new.language, kind=new.kind,
                change=SymbolChangeKind.SIGNATURE_CHANGED,
                strength=EvidenceStrength.VERIFIED,
                old_signature=old_sig, new_signature=new_sig,
                old_path=old.path, new_path=new.path,
                old_occurrence_id=old.occ_id,
                new_occurrence_id=new.occ_id))
        elif (old.content_hash and new.content_hash
                and old.content_hash != new.content_hash):
            changes.append(SymbolChange(
                symbol_key=k, qualified_name=new.qname,
                language=new.language, kind=new.kind,
                change=SymbolChangeKind.BODY_CHANGED,
                strength=EvidenceStrength.VERIFIED,
                old_signature=old_sig, new_signature=new_sig,
                old_path=old.path, new_path=new.path,
                old_occurrence_id=old.occ_id,
                new_occurrence_id=new.occ_id))
        # else: no_semantic_change — omitido do relatório por padrão.

    truncated = len(changes) > max_changes
    return tuple(changes[:max_changes]), tuple(unknown), truncated
