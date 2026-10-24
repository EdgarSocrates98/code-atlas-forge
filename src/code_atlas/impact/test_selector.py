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


def is_test_path(path: str) -> bool:
    p = path.replace("\\", "/").lower()
    name = p.rsplit("/", 1)[-1]
    return ((name.startswith("test_") and name.endswith(".py"))
            or name.endswith("_test.py")
            or any(name.endswith(s) for s in _TEST_SUFFIXES)
            or "/__tests__/" in p
            or "/tests/" in p or p.startswith("tests/"))


def select(
    conn: sqlite3.Connection, snapshot_id: int,
    symbol_changes: tuple[SymbolChange, ...],
    impacts: tuple[ImpactCandidate, ...],
    *,
    max_candidates: int = 50,
) -> tuple[TestCandidate, ...]:
    conn.row_factory = sqlite3.Row
    out: list[TestCandidate] = []
    seen: set[str] = set()

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
                    occurrence_id=c.occurrence_id))

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
