"""Medição de performance Cycle 4 (lf-0045).

p50/p95/p99 por cenário x estratégia, n=30 runs. Cenários:
- ``small``: fixture EVAL_REPO (~6 arquivos)
- ``medium``: corpus golden ``large``

Saída: ``benchmarks/results/lf-0045-perf-cycle4.json`` (commitado).
"""

from __future__ import annotations

import io
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from retrieval_eval import build_eval_repo

from code_atlas.context.contracts import ContextRequest
from code_atlas.context.engine import ContextEngine
from code_atlas.indexing import IndexCoordinator
from code_atlas.mcp import AtlasMcpServer
from code_atlas.search.contracts import SearchRequest
from code_atlas.search.engine import SearchEngine
from code_atlas.storage import connect, migrate

RUNS = 30
QUERIES = ("create_order", "OrderService", "charge", "validate")


def _pct(v: list[float], q: float) -> float:
    s = sorted(v)
    return s[min(len(s) - 1, int(q * len(s)))]


def _bench(fn) -> dict[str, float]:
    fn()  # warm-up
    lat = []
    for _ in range(RUNS):
        t0 = time.monotonic()
        fn()
        lat.append((time.monotonic() - t0) * 1000.0)
    return {
        "p50_ms": round(statistics.median(lat), 3),
        "p95_ms": round(_pct(lat, 0.95), 3),
        "p99_ms": round(_pct(lat, 0.99), 3),
        "runs": RUNS,
    }


def _scenario(conn, root: str) -> dict[str, Any]:
    eng = SearchEngine(conn)
    ctx = ContextEngine(conn)
    q = QUERIES[0]
    out: dict[str, Any] = {}
    for strat in ("structural", "lexical", "graph", "hybrid"):
        out[strat] = _bench(lambda s=strat: eng.search(SearchRequest(
            query=q, repository_root=root, strategy=s, limit=10)))
    out["pack-focus"] = _bench(lambda: ctx.build(ContextRequest(
        repository_root=root, task=q, level="focus")))
    out["pack-deep"] = _bench(lambda: ctx.build(ContextRequest(
        repository_root=root, task=q, level="deep")))
    # MCP end-to-end (in-process — transporte ndjson incluído)
    srv = AtlasMcpServer()
    real_ctx = srv._ctx
    srv._ctx = lambda pr: real_ctx({**dict(pr), "root": root})

    def mcp_call() -> None:
        srv.serve(io.StringIO(
            json.dumps({"jsonrpc": "2.0", "id": 1,
                        "method": "tools/call",
                        "params": {"name": "atlas_search",
                                   "arguments": {"query": q}}}) + "\n"),
            io.StringIO())

    out["mcp-search"] = _bench(mcp_call)
    return out


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="perf-c4-"))
    results: dict[str, Any] = {"tool": "code-atlas perf-cycle4",
                               "runs_per_cell": RUNS, "scenarios": {}}
    # small
    sroot = tmp / "small"
    conn = build_eval_repo(sroot)
    results["scenarios"]["small"] = _scenario(conn, str(sroot))
    conn.close()
    # medium — corpus golden "large"
    corpus = Path(__file__).parent / "corpus" / "large"
    if corpus.is_dir():
        mroot = tmp / "medium"
        import shutil
        shutil.copytree(corpus, mroot)
        conn = connect(tmp / "medium.db")
        migrate(conn)
        IndexCoordinator(conn).index(mroot)
        conn.row_factory = __import__("sqlite3").Row
        results["scenarios"]["medium"] = _scenario(conn, str(mroot))
        conn.close()
    out = Path(__file__).parents[1].parent / "benchmarks" / "results" / \
        "lf-0045-perf-cycle4.json"
    out.write_text(json.dumps(results, indent=1, sort_keys=True) + "\n")
    print("wrote", out)


if __name__ == "__main__":
    main()
