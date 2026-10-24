"""Harness de avaliação do corpus golden (lf-0013 / Phases 7-8).

Indexa cada corpus numa DB fresca, extrai os conjuntos reais de
symbols/relations do snapshot persistido e compara com o manifesto
``expected.json`` commitado — o manifesto é o ground truth congelado
(grammar drift aparece como diff de teste).

Métricas honestas:
- ``symbols``/``relations`` no modo ``all``: P/R sobre conjuntos exatos.
- modo ``subset``: recall sobre os itens listados + verificação exata
  dos contadores ``*_by_kind``/``*_by_type``.
- relations são sempre repartidas por ``confidence_class``
  (``resolved`` vs ``syntactic``) — nunca agregadas numa métrica única.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from code_atlas.indexing import IndexCoordinator
from code_atlas.storage import connect, migrate

CORPUS_DIR = Path(__file__).parent / "corpus"


@dataclass(frozen=True)
class PRF:
    tp: int
    fp: int
    fn: int

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 1.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 1.0


@dataclass
class CorpusReport:
    name: str
    symbols: dict[str, Any] = field(default_factory=dict)
    relations: dict[str, Any] = field(default_factory=dict)
    file_parse_status: dict[str, str] = field(default_factory=dict)
    missing_symbols: list[dict] = field(default_factory=list)
    missing_relations: list[dict] = field(default_factory=list)
    unexpected_symbols: list[dict] = field(default_factory=list)
    unexpected_relations: list[dict] = field(default_factory=list)
    count_mismatches: dict[str, Any] = field(default_factory=dict)
    file_status_mismatches: dict[str, Any] = field(default_factory=dict)


def _sym_key(s: dict[str, Any]) -> tuple:
    return (s["qname"], s["kind"], s["language"], s["path"])


def _rel_key(r: dict[str, Any]) -> tuple:
    to = r["to"] if r["to"] is not None else f"unresolved:{r['to_text']}"
    return (r["type"], r["from"], to, r["status"], r["confidence"])


def snapshot_sets(db_path: Path, repo: Path) -> tuple[set, set, dict[str, str], int]:
    """Indexa `repo` em DB fresca; retorna (symbols, relations,
    file_parse_status, snapshot_id) do snapshot READY."""
    conn = connect(db_path)
    migrate(conn)
    try:
        out = IndexCoordinator(conn).index(repo)
        if out.status != "success" or out.snapshot_id is None:
            raise AssertionError(f"index failed for {repo}: {out.error}")
        sid = out.snapshot_id
        syms = {
            (r["q"], r["k"], r["l"], r["p"])
            for r in conn.execute(
                "SELECT DISTINCT s.qualified_name q, s.kind k, s.language l, "
                "f.relative_path p FROM symbols s "
                "JOIN symbol_occurrences o ON o.logical_symbol_id=s.id "
                "JOIN source_files f ON f.id=o.source_file_id "
                "WHERE o.snapshot_id=?",
                (sid,),
            )
        }
        rels = {
            (r["t"], r["fq"],
             r["tq"] if r["tq"] is not None else f"unresolved:{r['tu']}",
             r["rs"], r["cc"])
            for r in conn.execute(
                "SELECT r.type t, sf.qualified_name fq, st.qualified_name tq, "
                "r.to_unresolved tu, r.resolution_status rs, "
                "r.confidence_class cc FROM relations r "
                "JOIN symbol_occurrences o1 ON o1.id=r.from_id "
                "JOIN symbols sf ON sf.id=o1.logical_symbol_id "
                "LEFT JOIN symbol_occurrences o2 ON o2.id=r.to_id "
                "LEFT JOIN symbols st ON st.id=o2.logical_symbol_id "
                "WHERE r.snapshot_id=?",
                (sid,),
            )
        }
        fstatus = {
            r["p"]: r["s"] for r in conn.execute(
                "SELECT DISTINCT f.relative_path p, o.parse_status s "
                "FROM symbol_occurrences o "
                "JOIN source_files f ON f.id=o.source_file_id "
                "WHERE o.snapshot_id=?",
                (sid,),
            )
        }
        return syms, rels, fstatus, sid
    finally:
        conn.close()


def _prf(expected: set, actual: set) -> PRF:
    return PRF(len(expected & actual), len(actual - expected),
               len(expected - actual))


def evaluate(corpus: Path, db_path: Path) -> CorpusReport:
    """Indexa um corpus e avalia contra expected.json."""
    expected = json.loads((CORPUS_DIR.parent / "expected" / f"{corpus.name}.json").read_text())
    syms, rels, fstatus, _sid = snapshot_sets(db_path, corpus)

    rep = CorpusReport(name=corpus.name, file_parse_status=fstatus)

    exp_syms = {_sym_key(s) for s in expected["symbols"]}
    if expected.get("symbols_mode") == "all":
        rep.symbols = vars(_prf(exp_syms, syms))
        rep.symbols |= {
            "precision": _prf(exp_syms, syms).precision,
            "recall": _prf(exp_syms, syms).recall,
        }
        rep.missing_symbols = [
            dict(zip(("qname", "kind", "language", "path"), k, strict=True))
            for k in sorted(exp_syms - syms)
        ]
        rep.unexpected_symbols = [
            dict(zip(("qname", "kind", "language", "path"), k, strict=True))
            for k in sorted(syms - exp_syms)
        ]
    else:  # subset: recall sobre o universo declarado + counts exatos
        rep.symbols = {"mode": "subset", "recall": _prf(exp_syms, syms).recall}
        rep.missing_symbols = [
            dict(zip(("qname", "kind", "language", "path"), k, strict=True))
            for k in sorted(exp_syms - syms)
        ]

    exp_rels = {_rel_key(r) for r in expected["relations"]}
    rep.relations = _relations_metrics(exp_rels, rels,
                                     expected.get("relations_mode"))
    if expected.get("relations_mode") == "all":
        rep.missing_relations = [_rel_row(k) for k in sorted(exp_rels - rels)]
        rep.unexpected_relations = [_rel_row(k) for k in sorted(rels - exp_rels)]
    else:
        rep.missing_relations = [_rel_row(k) for k in sorted(exp_rels - rels)]

    rep.count_mismatches = _count_mismatches(expected, syms, rels)
    exp_fs = expected.get("file_parse_status", {})
    rep.file_status_mismatches = {
        p: {"expected": s, "actual": fstatus.get(p)}
        for p, s in exp_fs.items()
        if fstatus.get(p) != s
    }
    return rep


def _rel_row(key: tuple) -> dict[str, Any]:
    t, fq, to, rs, cc = key
    d: dict[str, Any] = {"type": t, "from": fq, "status": rs,
                         "confidence": cc}
    if to.startswith("unresolved:"):
        d["to_text"] = to[len("unresolved:"):]
    else:
        d["to"] = to
    return d


def _relations_metrics(exp: set, act: set, mode: str | None) -> dict[str, Any]:
    """P/R geral + buckets por confidence — nunca agregados."""
    out: dict[str, Any] = {"mode": mode or "all"}
    overall = _prf(exp, act)
    out["all"] = {**vars(overall), "precision": overall.precision,
                  "recall": overall.recall}
    for bucket in ("resolved", "syntactic", "heuristic", "inferred"):
        e = {r for r in exp if r[4] == bucket}
        a = {r for r in act if r[4] == bucket}
        if not e and not a:
            continue
        m = _prf(e, a)
        out[bucket] = {**vars(m), "precision": m.precision,
                       "recall": m.recall}
    return out


def _count_mismatches(expected: dict[str, Any], syms: set,
                      rels: set) -> dict[str, Any]:
    """Verifica contadores exatos declarados no manifesto (modo subset)."""
    mismatches: dict[str, Any] = {}
    exp_sk = expected.get("symbols_by_kind")
    if exp_sk is not None:
        actual: dict[str, int] = {}
        for (_q, k, _l, _p) in syms:
            actual[k] = actual.get(k, 0) + 1
        if actual != exp_sk:
            mismatches["symbols_by_kind"] = {
                "expected": exp_sk, "actual": actual}
    exp_rt = expected.get("relations_by_type")
    if exp_rt is not None:
        actual = {}
        for (t, _f, _to, _rs, _cc) in rels:
            actual[t] = actual.get(t, 0) + 1
        if actual != exp_rt:
            mismatches["relations_by_type"] = {
                "expected": exp_rt, "actual": actual}
    return mismatches
