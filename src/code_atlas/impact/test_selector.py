"""Seleção de testes candidatos (Phase 7).

Evidências, em ordem de força:
1. aresta ``TESTS``/``CALLS``/``IMPORTS`` resolvida de arquivo de teste
   para símbolo alterado → ``direct``/``verified``;
2. consumidor transitivo localizado em arquivo de teste → ``transitive``;
3. convenção de nome no mesmo módulo (``test_foo.py`` p/ ``foo.py``) →
   ``heuristic`` — nunca declarado como cobertura real.

Comandos de execução são *sugestões* estáticas — nada é executado.
"""

from __future__ import annotations

import sqlite3

from code_atlas.impact.contracts import (
    EvidenceStrength,
    ImpactCandidate,
    SymbolChange,
    TestCandidate,
)

_TEST_SUFFIXES = (
    ".test.ts", ".spec.ts", ".test.tsx", ".spec.tsx",
)

# cap default de candidatos — declarado em ``truncation_reasons``
# quando a saída chega ao limite (lf-0151, convenção at-cap).
MAX_TEST_CANDIDATES = 50


def is_test_path(path: str) -> bool:
    p = path.replace("\\", "/").lower()
    name = p.rsplit("/", 1)[-1]
    return ((name.startswith("test_") and name.endswith(".py"))
            or name.endswith("_test.py")
            or any(name.endswith(s) for s in _TEST_SUFFIXES)
            or "/__tests__/" in p
            or "/tests/" in p or p.startswith("tests/"))


def _edge_candidates(
    conn: sqlite3.Connection, snapshot_id: int,
    symbol_changes: tuple[SymbolChange, ...],
    out: list[TestCandidate], seen: set[str],
    max_candidates: int,
    *,
    occs: set[int] | None = None,
    revision: str = "target",
) -> None:
    """Arestas resolvidas teste→alterado = evidência real de cobertura."""
    changed_occs = occs if occs is not None else {
        oid for s in symbol_changes
        for oid in (s.new_occurrence_id, s.old_occurrence_id) if oid}
    if not changed_occs:
        return
    marks = ",".join("?" for _ in changed_occs)
    edge_sql = (
        "SELECT r.from_id, r.type, r.resolution_status"
        " FROM relations r WHERE r.snapshot_id = ?"
        f" AND r.to_id IN ({marks})"
        " ORDER BY r.id LIMIT ?")
    rows = conn.execute(
        edge_sql,
        (snapshot_id, *sorted(changed_occs), max_candidates)
    ).fetchall()
    file_of: dict[int, sqlite3.Row | None] = {}
    for r in rows:
        fid = int(r["from_id"])
        if fid not in file_of:
            file_of[fid] = conn.execute(
                "SELECT f.relative_path, o.id AS occ FROM"
                " symbol_occurrences o JOIN source_files f"
                " ON f.id = o.source_file_id"
                " WHERE o.id = ?", (fid,)).fetchone()
        frow = file_of[fid]
        path = str(frow["relative_path"]) if frow else ""
        if frow and is_test_path(path) and path not in seen:
            seen.add(path)
            out.append(TestCandidate(
                test_file=path, test_symbol=None,
                relationship="direct",
                reason=f"aresta {r['type']}→símbolo alterado",
                strength=(EvidenceStrength.VERIFIED
                          if r["resolution_status"] == "resolved"
                          else EvidenceStrength.POSSIBLE),
                occurrence_id=int(frow["occ"]),
                revision=revision))


def select(
    conn: sqlite3.Connection, snapshot_id: int,
    symbol_changes: tuple[SymbolChange, ...],
    impacts: tuple[ImpactCandidate, ...],
    *,
    base_snapshot_id: int | None = None,
    max_candidates: int = MAX_TEST_CANDIDATES,
) -> tuple[TestCandidate, ...]:
    conn.row_factory = sqlite3.Row
    out: list[TestCandidate] = []
    seen: set[str] = set()

    # 0a) arestas resolvidas teste→símbolo-alterado no ALVO.
    target_occs = {
        oid for s in symbol_changes
        for oid in (s.new_occurrence_id,) if oid}
    _edge_candidates(
        conn, snapshot_id, symbol_changes, out, seen, max_candidates,
        occs=target_occs or None, revision="target")

    # 0b) lf-0080: arestas HISTÓRICAS na base — testes que referenciavam
    # símbolos removidos (ocorrência só existe na base).
    if base_snapshot_id is not None and base_snapshot_id != snapshot_id:
        base_occs = {
            s.old_occurrence_id for s in symbol_changes
            if s.old_occurrence_id is not None}
        _edge_candidates(
            conn, base_snapshot_id, symbol_changes, out, seen,
            max_candidates, occs=base_occs or None, revision="base")

    # 1) impact candidates que já vivem em arquivos de teste.
    for c in impacts:
        if c.source_path and is_test_path(c.source_path):
            rel = ("direct" if c.path and c.path.hops == 1
                   else "transitive")
            if c.source_path not in seen:
                seen.add(c.source_path)
                out.append(TestCandidate(
                    test_file=c.source_path,
                    test_symbol=c.qualified_name,
                    relationship=rel,
                    reason=f"grafo: {c.path.reason if c.path else ''}",
                    strength=c.strength,
                    occurrence_id=c.occurrence_id,
                    revision=("base" if c.consumer_state else "target")))

    # 2) convenção de nome: test_<mod>.py / <mod>.test.ts por arquivo
    # de origem alterado.
    changed_paths = {s.new_path or s.old_path for s in symbol_changes}
    rows = conn.execute(
        "SELECT DISTINCT f.relative_path, o.id"
        " FROM symbol_occurrences o"
        " JOIN source_files f ON f.id = o.source_file_id"
        " WHERE o.snapshot_id = ? ORDER BY f.relative_path",
        (snapshot_id,)).fetchall()
    stems = {p.rsplit("/", 1)[-1].rsplit(".", 1)[0]
             for p in changed_paths if p}
    for r in rows:
        path = str(r["relative_path"])
        if path in seen or not is_test_path(path):
            continue
        name = path.rsplit("/", 1)[-1]
        stem = name.replace("test_", "").replace("_test", "")
        stem = stem.split(".")[0]
        if stem and stem in stems:
            seen.add(path)
            out.append(TestCandidate(
                test_file=path, test_symbol=None,
                relationship="heuristic",
                reason="convenção de nome sobre módulo alterado",
                strength=EvidenceStrength.POSSIBLE,
                occurrence_id=int(r["id"])))
        if len(out) >= max_candidates:
            break
    return tuple(out[:max_candidates])
