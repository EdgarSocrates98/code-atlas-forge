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
    # lf-0024: buckets aditivos — nunca misturam classes de evidência
    resolver_metrics: dict[str, Any] = field(default_factory=dict)
    status_rates: dict[str, Any] = field(default_factory=dict)
    false_positive: dict[str, Any] = field(default_factory=dict)


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
    syms, rels, fstatus, sid = snapshot_sets(db_path, corpus)

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
    rep.resolver_metrics = _resolver_metrics(db_path, sid, exp_rels)
    rep.status_rates = _status_rates(rels)
    # fp só tem denominador honesto quando o manifesto declara o
    # universo completo (modo "all"); em subset seria ruído.
    if expected.get("relations_mode") == "all":
        rep.false_positive = _false_positive(exp_rels, rels)
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


# --- lf-0024: métricas por resolver / status / falsos-positivos -------


def _resolver_metrics(
    db_path: Path, snapshot_id: int, exp_rels: set
) -> dict[str, Any]:
    """P/R por ``relations.resolver`` — classe de evidência nunca
    agregada com outras (grill gate lf-0024)."""
    conn = connect(db_path, read_only=True)
    try:
        rows = conn.execute(
            "SELECT r.resolver rv, r.type t, sf.qualified_name fq, "
            "st.qualified_name tq, r.to_unresolved tu, "
            "r.resolution_status rs, r.confidence_class cc "
            "FROM relations r "
            "JOIN symbol_occurrences o1 ON o1.id=r.from_id "
            "JOIN symbols sf ON sf.id=o1.logical_symbol_id "
            "LEFT JOIN symbol_occurrences o2 ON o2.id=r.to_id "
            "LEFT JOIN symbols st ON st.id=o2.logical_symbol_id "
            "WHERE r.snapshot_id=?",
            (snapshot_id,),
        ).fetchall()
    finally:
        conn.close()
    by_resolver: dict[str, set] = {}
    for r in rows:
        to = r["tq"] if r["tq"] is not None else f"unresolved:{r['tu']}"
        by_resolver.setdefault(r["rv"], set()).add(
            (r["t"], r["fq"], to, r["rs"], r["cc"])
        )
    return {
        rv: {
            **vars(m := _prf(exp_rels, rels)),
            "precision": m.precision,
            "recall": m.recall,
        }
        for rv, rels in sorted(by_resolver.items())
    }


def _status_rates(rels: set) -> dict[str, Any]:
    """Rates de desfecho sobre as relações reais — denominador explícito."""
    total = len(rels)
    out: dict[str, Any] = {"total": total}
    for status in ("resolved", "ambiguous", "unresolved", "external"):
        n = sum(1 for r in rels if r[3] == status)
        out[status] = {"n": n, "rate": (n / total) if total else 0.0}
    return out


def _false_positive(exp_rels: set, rels: set) -> dict[str, Any]:
    """Falsos-positivos = arestas 'resolved' ausentes do ground truth.

    Só conta arestas com ``resolution_status == 'resolved'`` — um
    'unresolved'/'ambiguous' inesperado não é link falso, é label
    honesto.
    """
    resolved_actual = {r for r in rels if r[3] == "resolved"}
    fp = resolved_actual - exp_rels
    return {
        "n": len(fp),
        "rate": (len(fp) / len(resolved_actual)) if resolved_actual else 0.0,
        "edges": [_rel_row(k) for k in sorted(fp)],
    }


# --- lf-0024: invalidation + reuse correctness ------------------------


def evaluate_incremental(
    corpus: Path, db_path: Path, mutate: Any, restore: Any
) -> dict[str, Any]:
    """Par incremental x full sobre uma mutação do corpus.

    ``mutate()``/``restore()`` são callables que alteram/revertem
    arquivos do corpus. Retorna: plano do run incremental, igualdade
    canônica incremental x full, e igualdade de ids lógicos para
    arquivos inalterados (reuse correctness).
    """
    conn = connect(db_path)
    migrate(conn)
    try:
        coord = IndexCoordinator(conn)
        base = coord.index(corpus)
        assert base.status == "success" and base.snapshot_id is not None
        base_syms = _symbol_ids(conn, base.snapshot_id)

        mutate()
        try:
            inc = coord.index(corpus, incremental=True)
            plan_actions = {
                f.path: str(f.action) for f in (coord.last_plan.files if coord.last_plan else ())
            }
            full = coord.index(corpus, force=True)
            canon_equal = (
                _canonical(conn, inc.snapshot_id)  # type: ignore[arg-type]
                == _canonical(conn, full.snapshot_id)  # type: ignore[arg-type]
            )
        finally:
            restore()

        # reuse correctness: símbolos de arquivos que o plano marcou
        # ``reuse`` mantêm o mesmo logical_symbol_id do snapshot base
        reused = {p for p, a in plan_actions.items() if a == "reuse"}
        inc_syms = _symbol_ids(conn, inc.snapshot_id)  # type: ignore[arg-type]
        reused_id_stable = all(
            base_syms.get(p) == inc_syms.get(p) for p in reused
        )
        return {
            "plan_actions": plan_actions,
            "incremental_matches_full": canon_equal,
            "reused_files": sorted(reused),
            "reuse_symbol_ids_stable": reused_id_stable,
        }
    finally:
        conn.close()


def _symbol_ids(conn, snapshot_id: int) -> dict[str, frozenset]:
    """path → frozenset de logical_symbol_ids (occurrences do snapshot)."""
    rows = conn.execute(
        "SELECT f.relative_path p, o.logical_symbol_id sid "
        "FROM symbol_occurrences o "
        "JOIN source_files f ON f.id=o.source_file_id "
        "WHERE o.snapshot_id=?",
        (snapshot_id,),
    ).fetchall()
    out: dict[str, set] = {}
    for r in rows:
        out.setdefault(r["p"], set()).add(r["sid"])
    return {p: frozenset(v) for p, v in out.items()}


def _canonical(conn, snapshot_id: int) -> dict[str, set]:
    """Espelho do helper canônico de test_incremental."""
    occs = conn.execute(
        "SELECT s.kind, s.qualified_name, s.language, sf.relative_path, "
        "o.parse_status, o.content_hash FROM symbol_occurrences o "
        "JOIN symbols s ON s.id = o.logical_symbol_id "
        "JOIN source_files sf ON sf.id = o.source_file_id "
        "WHERE o.snapshot_id=?",
        (snapshot_id,),
    ).fetchall()
    nodes = {
        (r[0], r[1], r[2], r[3], r[4], r[5]) for r in occs
    }
    rels = conn.execute(
        "SELECT r.type, r.resolution_status, r.confidence_class, "
        "sff.relative_path fp, sf.qualified_name fq, "
        "sft.relative_path tp, st.qualified_name tq, r.to_unresolved "
        "FROM relations r "
        "JOIN symbol_occurrences of_ ON of_.id=r.from_id "
        "JOIN source_files sff ON sff.id=of_.source_file_id "
        "JOIN symbols sf ON sf.id=of_.logical_symbol_id "
        "LEFT JOIN symbol_occurrences ot ON ot.id=r.to_id "
        "LEFT JOIN source_files sft ON sft.id=ot.source_file_id "
        "LEFT JOIN symbols st ON st.id=ot.logical_symbol_id "
        "WHERE r.snapshot_id=?",
        (snapshot_id,),
    ).fetchall()
    edges = {
        (r[0], r[3], r[4], r[5], r[6], r[7] or "", r[1], r[2])
        for r in rels
    }
    return {"nodes": nodes, "edges": edges}
