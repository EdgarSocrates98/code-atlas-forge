#!/usr/bin/env python3
"""Benchmark do pipeline de indexação (Cycle 2 Phase 8, lf-0013).

Mede discovery, parse, graph-build, persist e tempo total sobre corpora
sintéticos deterministicamente seeded (mesmo seed → mesma árvore), mais:

- p50/p95 de latência de query (StructuralSearch) sobre o snapshot pronto
- tempo de reindex no-op (digest+versões inalterados)
- peak memory via tracemalloc (processo corrente)
- tamanho do DB em disco
- contagem real de símbolos/arestas persistidas
- hardware/OS/python do ambiente

Honestidade: o baseline Cycle-1 media discovery+hash somente — o número
total aqui NÃO é comparável ao ``files_per_s`` de bench_scan; comparação
válida é apenas na fase ``discovery_s``.

Uso::

    .venv/bin/python benchmarks/bench_index.py --sizes 100 1000 5000
    .venv/bin/python benchmarks/bench_index.py --sizes 100 --json out.json
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import resource
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from code_atlas.discovery import ScanOptions, scan
from code_atlas.graph.builder import GraphBuilder
from code_atlas.indexing import IndexCoordinator
from code_atlas.parsers.engine import ParseEngine
from code_atlas.search.structural import SearchFilters, StructuralSearch
from code_atlas.storage import connect, migrate

SEED = 20261024

PY_BODY = '''"""mod{i} — corpus sintético seeded."""
from pkg{n}.util import helper{n}


class K{i}(Base{n}):
    def m{i}(self):
        return helper{n}() + phantom{n}()


def f{i}(a, b):
    return a + b
'''


def synth_indexable_corpus(root: Path, n_files: int, seed: int = SEED) -> Path:
    """Corpus Python parseável: imports + herança + calls (resolvidos e
    não-resolvidos) — exercita parse+grafo de verdade."""
    rng = random.Random(seed)
    corpus = root / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    for i in range(n_files):
        d = corpus / f"pkg{rng.randint(0, 9)}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "__init__.py").write_text("")
        body = PY_BODY.format(i=i, n=rng.randint(0, 9))
        (d / f"mod{i}.py").write_text(body)
    return corpus


def _peak_rss_mb() -> float:
    """ru_maxrss: KiB no Linux, bytes no macOS."""
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / 1024 if sys.platform != "darwin" else rss / (1024 * 1024)


def _p(vals: list[float], pct: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[min(len(s) - 1, int(len(s) * pct))]


def bench_size(tmp: Path, n_files: int) -> dict[str, object]:
    corpus = synth_indexable_corpus(tmp / f"run{n_files}", n_files)
    db_path = tmp / f"atlas-{n_files}.db"
    conn = connect(db_path)
    migrate(conn)
    coord = IndexCoordinator(conn)

    tracemalloc.start()
    t0 = time.monotonic()
    out = coord.index(corpus)
    total_s = time.monotonic() - t0
    _, tracemalloc_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    if out.status != "success":
        raise RuntimeError(f"index failed: {out.error}")

    # fases isoladas (mesma entrada, medidas fora do coordinator)
    t = time.monotonic()
    scan_res = scan(corpus, ScanOptions())
    discovery_s = time.monotonic() - t

    engine = ParseEngine()
    t = time.monotonic()
    results = [
        engine.parse(f.path, (corpus / f.path).read_bytes())
        for f in scan_res.files
        if f.path.endswith((".py", ".ts", ".tsx"))
    ]
    parse_s = time.monotonic() - t

    t = time.monotonic()
    graph = GraphBuilder().build(corpus.name, scan_res, results)
    graph_s = time.monotonic() - t
    persist_s = total_s - discovery_s - parse_s - graph_s  # residual honesto

    # no-op reindex (digest inalterado → sem reparse)
    t = time.monotonic()
    noop = coord.index(corpus)
    noop_s = time.monotonic() - t

    # latência de query p50/p95 — busca real sobre o snapshot pronto
    search = StructuralSearch(conn)
    repo_id = search.repo_for_root(str(corpus.resolve()))
    snap, _ = search.resolve_snapshot(repo_id, None)  # type: ignore[arg-type]
    lat: list[float] = []
    for i in range(60):
        q = f"f{i % n_files}"
        t = time.monotonic()
        search.search_symbols(repo_id, snap.id, q, SearchFilters(), 50, 0)  # type: ignore[arg-type]
        lat.append(time.monotonic() - t)

    stats = dict(out.stats) if out.stats else {}
    return {
        "files": n_files,
        "total_s": round(total_s, 4),
        "discovery_s": round(discovery_s, 4),
        "parse_s": round(parse_s, 4),
        "graph_s": round(graph_s, 4),
        "persist_residual_s": round(max(persist_s, 0.0), 4),
        "noop_reindex_s": round(noop_s, 4),
        "noop_status": noop.status,
        "query_latency_s": {
            "p50": round(_p(lat, 0.50), 6),
            "p95": round(_p(lat, 0.95), 6),
            "n": len(lat),
        },
        "peak_tracemalloc_mb": round(tracemalloc_peak / 1e6, 1),
        "peak_rss_mb": round(_peak_rss_mb(), 1),
        "db_size_mb": round(
            sum(
                p.stat().st_size
                for p in (db_path, Path(str(db_path) + "-wal"))
                if p.exists()
            )
            / 1e6,
            2,
        ),
        "symbols": stats.get("symbols_new", 0),
        "occurrences": stats.get("occurrences", 0),
        "relations": stats.get("relations", 0),
        "edges_skipped": stats.get("edges_skipped", 0),
        "files_parsed": stats.get("files_parsed", 0),
        "graph_nodes": graph.stats.get("nodes", 0),
        "graph_edges": graph.stats.get("edges", 0),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Benchmark do índice Code Atlas")
    p.add_argument("--sizes", type=int, nargs="+", default=[100, 1000, 5000])
    p.add_argument("--json", type=Path, default=None,
                   help="grava resultado em arquivo JSON")
    args = p.parse_args()

    env = {
        "tool": "code-atlas bench_index",
        "seed": SEED,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "baseline_note": (
            "Cycle-1 bench_scan media discovery+hash apenas; comparável "
            "somente em discovery_s."
        ),
        "runs": [],
    }
    for n in args.sizes:
        with tempfile.TemporaryDirectory(prefix=f"atlas-bench-{n}-") as td:
            env["runs"].append(bench_size(Path(td), n))

    text = json.dumps(env, indent=2, sort_keys=True)
    if args.json:
        args.json.write_text(text + "\n")
        print(f"written: {args.json}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
