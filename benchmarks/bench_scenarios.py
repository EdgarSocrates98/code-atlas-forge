#!/usr/bin/env python3
"""Benchmark de cenários incrementais (Cycle 3 Phase 8, lf-0023).

Matriz de 9 cenários sobre corpora ``synth_indexable_corpus`` (seed
fixo, corpus_version=2):

    cold_index · noop · single_file_change · imported_module_change ·
    rename · delete · parser_version_change · resolver_version_change ·
    full_rebuild

Para cada cenário mutante mede-se o ``index()`` incremental e um
``index(force=True)`` sobre **o mesmo estado do corpus** — resposta a
duas perguntas: quanto o incremental economiza e se o resultado é
**canonicamente idêntico** ao full (mesmos nós/arestas do snapshot).

Mutações são revertidas e o índice restaurado ao baseline entre
cenários (não-timed) para que cada plano seja relativo ao mesmo estado.

Uso::

    .venv/bin/python benchmarks/bench_scenarios.py --sizes 100 1000 5000
    .venv/bin/python benchmarks/bench_scenarios.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import platform
import sqlite3
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench_index import SEED, synth_indexable_corpus

from code_atlas.indexing import IndexCoordinator
from code_atlas.parsers import tree_sitter_runtime as rt
from code_atlas.storage import connect, migrate

CORPUS_VERSION = 2


def _canonical(db: sqlite3.Connection, snapshot_id: int) -> dict[str, set]:
    """Nós/arestas canônicos do snapshot — espelho do helper de teste."""
    occs = db.execute(
        """
        SELECT s.kind, s.qualified_name, s.language, sf.relative_path,
               o.parse_status, o.content_hash
          FROM symbol_occurrences o
          JOIN symbols s ON s.id = o.logical_symbol_id
          JOIN source_files sf ON sf.id = o.source_file_id
         WHERE o.snapshot_id = ?
        """,
        (snapshot_id,),
    ).fetchall()
    nodes = {
        (r["kind"], r["qualified_name"], r["language"],
         r["relative_path"], r["parse_status"], r["content_hash"])
        for r in occs
    }
    rels = db.execute(
        """
        SELECT r.type, r.resolution_status, r.confidence_class,
               sf_from.relative_path AS fp,
               s_from.qualified_name AS fq,
               sf_to.relative_path AS tp,
               s_to.qualified_name AS tq,
               r.to_unresolved
          FROM relations r
          JOIN symbol_occurrences o_from ON o_from.id = r.from_id
          JOIN source_files sf_from ON sf_from.id = o_from.source_file_id
          JOIN symbols s_from ON s_from.id = o_from.logical_symbol_id
          LEFT JOIN symbol_occurrences o_to ON o_to.id = r.to_id
          LEFT JOIN source_files sf_to ON sf_to.id = o_to.source_file_id
          LEFT JOIN symbols s_to ON s_to.id = o_to.logical_symbol_id
         WHERE r.snapshot_id = ?
        """,
        (snapshot_id,),
    ).fetchall()
    edges = {
        (r["type"], r["fp"], r["fq"], r["tp"], r["tq"],
         r["to_unresolved"] or "", r["resolution_status"],
         r["confidence_class"])
        for r in rels
    }
    return {"nodes": nodes, "edges": edges}


def _timed_index(coord: IndexCoordinator, corpus: Path, **kw):
    t0 = time.monotonic()
    out = coord.index(corpus, **kw)
    return out, time.monotonic() - t0


def _plan_hist(coord: IndexCoordinator) -> dict[str, int]:
    plan = coord.last_plan
    if plan is None:
        return {}
    return dict(Counter(str(f.action) for f in plan.files))


def _rec(out, seconds: float, coord: IndexCoordinator) -> dict[str, object]:
    stats = dict(out.stats or {})
    return {
        "index_s": round(seconds, 4),
        "status": out.status,
        "noop": bool(getattr(out, "noop", False)),
        "files_parsed": stats.get("files_parsed", 0),
        "files_reused": stats.get("files_reused", 0),
        "cache_puts": stats.get("cache_puts", 0),
        "occurrences": stats.get("occurrences", 0),
        "relations": stats.get("relations", 0),
        "plan_actions": _plan_hist(coord),
    }


class _Mut:
    """Mutação reversível do corpus (context manager)."""

    def __init__(self, corpus: Path, name: str):
        self.corpus = corpus
        self.name = name
        self._undo = None
        self._patch = None

    def _mod(self) -> Path:
        """Um ``mod*.py`` real — o gerador os distribui por pkgs ao acaso."""
        return next(iter(sorted(self.corpus.rglob("mod*.py"))))

    def __enter__(self):
        c = self.corpus
        if self.name == "single_file_change":
            p = self._mod()
            old = p.read_text()
            p.write_text(old + "\n\ndef added_fn():\n    return 1\n")
            self._undo = lambda: p.write_text(old)
        elif self.name == "imported_module_change":
            p = c / "pkg3" / "util.py"
            old = p.read_text()
            p.write_text(old.replace("return 3", "return 33"))
            self._undo = lambda: p.write_text(old)
        elif self.name == "rename":
            src = self._mod()
            dst = src.with_name("renamed_mod.py")
            src.rename(dst)
            self._undo = lambda: dst.rename(src)
        elif self.name == "delete":
            p = self._mod()
            old = p.read_bytes()
            p.unlink()
            self._undo = lambda: p.write_bytes(old)
        elif self.name == "parser_version_change":
            # simula upgrade do tree-sitter-python — cache key + manifest
            self._patch = mock.patch.object(
                rt, "parser_version", return_value="99.0.0"
            )
            self._patch.start()
            self._undo = self._patch.stop
        elif self.name == "resolver_version_change":
            self._patch = mock.patch(
                "code_atlas.indexing.coordinator._SYN_RESOLVER_VERSION",
                "9.9.9",
            )
            self._patch.start()
            self._undo = self._patch.stop
        return self

    def __exit__(self, *exc):
        if self._undo is not None:
            self._undo()
        return False


MUTATION_SCENARIOS = (
    "single_file_change",
    "imported_module_change",
    "rename",
    "delete",
    "parser_version_change",
    "resolver_version_change",
)


def bench_size(tmp: Path, n_files: int) -> dict[str, object]:
    corpus = synth_indexable_corpus(tmp / f"run{n_files}", n_files)
    db_path = tmp / f"atlas-{n_files}.db"
    conn = connect(db_path)
    migrate(conn)
    coord = IndexCoordinator(conn)
    runs: dict[str, object] = {}

    # 1. cold index — baseline
    out, secs = _timed_index(coord, corpus)
    if out.status != "success":
        raise RuntimeError(f"cold index failed: {out.error}")
    runs["cold_index"] = _rec(out, secs, coord)
    base_snap = out.snapshot_id

    # 2. no-op
    out, secs = _timed_index(coord, corpus)
    runs["noop"] = _rec(out, secs, coord)

    # 3-8. mutações: incremental + full sobre o mesmo estado + igualdade
    for name in MUTATION_SCENARIOS:
        with _Mut(corpus, name):
            inc, inc_s = _timed_index(coord, corpus, incremental=True)
            rec = _rec(inc, inc_s, coord)
            full, full_s = _timed_index(coord, corpus, force=True)
            rec["full_index_s"] = round(full_s, 4)
            rec["matches_full"] = (
                _canonical(conn, inc.snapshot_id)
                == _canonical(conn, full.snapshot_id)
            )
            runs[name] = rec
        # restaura baseline (não-timed) — plano do próximo cenário fica
        # relativo ao estado do cold index
        coord.index(corpus)

    # 9. full rebuild sobre corpus pristine
    out, secs = _timed_index(coord, corpus, force=True)
    rec = _rec(out, secs, coord)
    rec["matches_cold"] = (
        _canonical(conn, out.snapshot_id) == _canonical(conn, base_snap)
    )
    runs["full_rebuild"] = rec

    return {
        "files": n_files,
        "runs": runs,
        "db_size_mb": round(
            sum(
                p.stat().st_size
                for p in (db_path, Path(str(db_path) + "-wal"))
                if p.exists()
            ) / 1e6,
            2,
        ),
    }


def main() -> int:
    p = argparse.ArgumentParser(
        description="Benchmark de cenários incrementais do Code Atlas"
    )
    p.add_argument("--sizes", type=int, nargs="+", default=[100, 1000, 5000])
    p.add_argument("--json", type=Path, default=None,
                   help="grava resultado em arquivo JSON")
    args = p.parse_args()

    env = {
        "tool": "code-atlas bench_scenarios",
        "seed": SEED,
        "corpus_version": CORPUS_VERSION,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "runs": [],
    }
    for n in args.sizes:
        with tempfile.TemporaryDirectory(prefix=f"atlas-scen-{n}-") as td:
            env["runs"].append(bench_size(Path(td), n))
            print(f"done: {n}", file=sys.stderr)

    text = json.dumps(env, indent=2, sort_keys=True)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(text + "\n")
        print(f"written: {args.json}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
