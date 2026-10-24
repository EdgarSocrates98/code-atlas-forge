"""Medição de performance Cycle 4.1 (lf-0056 — Phase 9).

Novos estágios medidos sobre o fixture EVAL_REPO:
- candidate generation híbrida (RRF + rerank on/off)
- evidence grounding (occurrence_ref validado)
- context build + enforcement do payload serializado
- serialization (render_json) + token estimation
- MCP tools/call atlas_evidence (grounded refs)

Saída: ``benchmarks/results/lf-0056-perf-cycle41.json`` (commitado).
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
from code_atlas.context.renderers import render_json
from code_atlas.context.tokenizer import HeuristicTokenizer
from code_atlas.mcp import AtlasMcpServer
from code_atlas.search.contracts import SearchRequest
from code_atlas.search.engine import SearchEngine
from code_atlas.search.refs import EvidenceGrounder

RUNS = 20
Q = "charge"


def _pct(v: list[float], q: float) -> float:
    s = sorted(v)
    return s[min(len(s) - 1, int(q * len(s)))]


def _bench(fn) -> dict[str, Any]:
    fn()
    lat = []
    for _ in range(RUNS):
        t0 = time.monotonic()
        fn()
        lat.append((time.monotonic() - t0) * 1000.0)
    return {"p50_ms": round(statistics.median(lat), 3),
            "p95_ms": round(_pct(lat, 0.95), 3),
            "p99_ms": round(_pct(lat, 0.99), 3),
            "runs": RUNS}


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="perf-c41-"))
    root = tmp / "repo"
    conn = build_eval_repo(root)
    roots = str(root.resolve())

    eng_v1 = SearchEngine(conn, enable_rerank=False)
    eng_v2 = SearchEngine(conn, enable_rerank=True)
    ctx_eng = ContextEngine(conn)
    grounder = EvidenceGrounder(conn)
    tok = HeuristicTokenizer()
    occ_id = int(conn.execute(
        "SELECT MIN(id) FROM symbol_occurrences").fetchone()[0])

    out: dict[str, Any] = {
        "tool": "code-atlas perf-cycle4.1",
        "runs_per_cell": RUNS,
        "fixture": "EVAL_REPO (~7 arquivos, py+ts)",
        "stages": {}}
    st = out["stages"]

    st["hybrid-candidates-rrf"] = _bench(lambda: eng_v1.search(
        SearchRequest(query=Q, strategy="hybrid",
                      repository_root=roots, limit=10)))
    st["hybrid-candidates-rrf+rerank"] = _bench(lambda: eng_v2.search(
        SearchRequest(query=Q, strategy="hybrid",
                      repository_root=roots, limit=10)))
    st["evidence-occurrence-ref"] = _bench(
        lambda: grounder.occurrence_ref(1, 1, occ_id))
    pack = ctx_eng.build(ContextRequest(
        task=Q, repository_root=roots, budget_tokens=2000))
    ctx2 = ContextEngine(conn)  # sem cache — mede o build real
    ctx2._pack_cache = {}
    st["context-build-serialized-enforce"] = _bench(
        lambda: ctx2.build(ContextRequest(
            task=Q, repository_root=roots, budget_tokens=2000)))
    st["render-json"] = _bench(lambda: render_json(pack))
    st["token-estimate-pack"] = _bench(
        lambda: tok.count(render_json(pack)))

    srv = AtlasMcpServer()
    real_ctx = srv._ctx
    srv._ctx = lambda pr: real_ctx({**dict(pr), "root": roots})

    def mcp_evidence() -> None:
        srv.serve(io.StringIO(json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "atlas_evidence",
                        "arguments": {
                            "name": "pkg.orders.OrderService.create_order"}}})
            + "\n"), io.StringIO())

    st["mcp-atlas-evidence"] = _bench(mcp_evidence)

    dst = Path("benchmarks/results/lf-0056-perf-cycle41.json")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"wrote {dst}")
    for k, v in st.items():
        print(f"{k:36s} p50={v['p50_ms']:7.3f} p95={v['p95_ms']:7.3f}")


if __name__ == "__main__":
    main()
