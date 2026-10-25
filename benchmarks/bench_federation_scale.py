#!/usr/bin/env python3
"""Benchmark de escala federada (lf-0134, Cycle 8 Phase 8).

Workspace multi-repo construído pela **CLI real** (``code-atlas index
--domains`` + ``code-atlas federation create/add/pin/view``) sobre
fixtures pequenos gerados deterministicamente (seed fixo). Mede:

- ``resolve_view`` — custo da materialização ``{repo: snapshot}`` +
  cache key completa (membership + identidade + pins + versões +
  schema);
- ``entity_occurrences`` (lookup por ``shared_key``), ``graph``
  (traversal BFS federado) e ``FederatedSearchEngine.search`` —
  em workspaces de 2..N membros (escala) e com narrowing a 1 membro
  (1 vs N dentro da mesma view);
- **verificação de isolamento**: query pinada ao snapshot S1 do
  repo A depois que S2 virou READY — o script falha (exit 1) se
  qualquer item carregar ``snapshot_id`` fora da view pinada.

Honestidade: números medidos, nunca metas. Fixtures são pequenos por
desenho — o regime medido é o fan-out N-way da federação, não o
tamanho do índice. ``--keep`` preserva o workspace para inspeção.

Uso::

    .venv/bin/python benchmarks/bench_federation_scale.py
    .venv/bin/python benchmarks/bench_federation_scale.py --repos 5 --repeats 9
    .venv/bin/python benchmarks/bench_federation_scale.py --json out.json
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import platform
import random
import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

SEED = 20261025

_OPENAPI = """\
openapi: 3.0.0
info:
  title: Orders API
  version: 1.0.0
servers:
  - url: https://api.orders.example/v1
paths:
  /orders:
    get:
      operationId: listOrders
      responses:
        '200':
          description: all orders
"""


def _repo_files(i: int, rng: random.Random) -> dict[str, str]:
    """Repo ``i``: contrato OpenAPI + ``mart.orders`` compartilhados
    (shared_key cross-repo) + ``mart.t{i}``/``mart.v{i}`` exclusivos."""
    files = {
        "spec/openapi.yaml": _OPENAPI,
        "q.sql": (
            "SELECT * FROM mart.orders;\n"
            f"SELECT * FROM mart.t{i};\n"
            f"CREATE VIEW mart.v{i} AS SELECT * FROM mart.orders;\n"
        ),
        "app/mod.py": (
            "def shared_handler():\n"
            f"    return {i}\n"
        ),
    }
    # variância determinística de tamanho — alguns repos maiores
    for j in range(1 + i % 3):
        files[f"sql/q{j}.sql"] = (
            "SELECT o.id, t.v\n"
            "FROM mart.orders o\n"
            f"JOIN mart.t{i} t ON t.id = o.id\n"
            f"WHERE o.flag = {rng.randrange(100)};\n"
        )
    return files


def _write_repo(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def _cli(argv: list[str]) -> dict:
    """Invoca ``code_atlas.cli.main`` capturando o payload ``--json``."""
    from code_atlas import cli

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.main(argv)
    if rc != 0:
        raise SystemExit(f"cli {' '.join(argv)} failed (rc={rc})")
    out = buf.getvalue().strip()
    return json.loads(out) if out else {}


def _time(fn: Callable[[], object], repeats: int) -> dict[str, float]:
    """``repeats`` execuções → median/min/p95/max em milissegundos."""
    samples: list[float] = []
    for _ in range(max(1, repeats)):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    samples.sort()
    p95 = samples[min(len(samples) - 1, int(len(samples) * 0.95))]
    return {
        "median_ms": round(statistics.median(samples), 3),
        "min_ms": round(samples[0], 3),
        "p95_ms": round(p95, 3),
        "max_ms": round(samples[-1], 3),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repos", type=int, default=5,
                    help="membros do workspace federado (default 5)")
    ap.add_argument("--repeats", type=int, default=9,
                    help="repetições por medição (default 9)")
    ap.add_argument("--json", metavar="PATH", help="escreve o resultado em JSON")
    ap.add_argument("--keep", action="store_true",
                    help="preserva o workspace temporário")
    args = ap.parse_args()
    if args.repos < 2:
        ap.error("--repos >= 2 (federação exige >= 2 membros)")

    workdir = Path(tempfile.mkdtemp(prefix="atlas-fed-bench-"))
    os.environ["CODE_ATLAS_CONFIG_DIR"] = str(workdir / "cfg")
    os.environ["CODE_ATLAS_DATA_DIR"] = str(workdir / "data")
    try:
        result = _run(args, workdir)
    finally:
        if not args.keep:
            import shutil
            shutil.rmtree(workdir, ignore_errors=True)
    if args.keep:
        result["workdir"] = str(workdir)
    out = json.dumps(result, indent=2, sort_keys=True)
    if args.json:
        Path(args.json).write_text(out + "\n")
        print(f"wrote {args.json}")
    else:
        print(out)
    return 0


def _run(args: argparse.Namespace, workdir: Path) -> dict:
    from code_atlas import cli
    from code_atlas.storage import connect, migrate

    rng = random.Random(SEED)
    t0 = time.perf_counter()

    # --- construção via CLI real -------------------------------------
    snaps: list[int] = []
    repos: list[int] = []
    for i in range(args.repos):
        root = _write_repo(workdir / f"repo_{i}", _repo_files(i, rng))
        payload = _cli(
            ["index", str(root), "--domains", "sql,api", "--json"])
        assert payload["snapshot_id"], payload
        snaps.append(int(payload["snapshot_id"]))
    _cli(["federation", "create", "scale", "--json"])
    for i in range(args.repos):
        payload = _cli(
            ["federation", "add", "scale", str(workdir / f"repo_{i}"),
             "--json"])
        repos.append(int(payload["repository_id"]))
    # workspaces parciais 2..N para a curva de escala
    for k in range(2, args.repos):
        _cli(["federation", "create", f"scale-{k}", "--json"])
        for i in range(k):
            _cli(
                ["federation", "add", f"scale-{k}", str(repos[i]),
                 "--json"])
    setup_s = round(time.perf_counter() - t0, 3)

    conn = connect(cli.db_path())
    migrate(conn)
    try:
        return _measure(args, conn, repos, snaps, workdir, setup_s)
    finally:
        conn.close()


def _measure(
    args: argparse.Namespace,
    conn,
    repos: list[int],
    snaps: list[int],
    workdir: Path,
    setup_s: float,
) -> dict:
    from code_atlas.federation import Federation, shared_key_of
    from code_atlas.search import SearchRequest
    from code_atlas.search.federated import FederatedSearchEngine

    fed = Federation(conn)
    eng = FederatedSearchEngine(conn)
    rep = max(1, args.repeats)

    skey_orders_row = conn.execute(
        "SELECT identity_key FROM domain_entities"
        " WHERE namespace = 'mart' AND qualified_name = 'orders' LIMIT 1"
    ).fetchone()
    assert skey_orders_row is not None
    skey_orders = shared_key_of(str(skey_orders_row["identity_key"]))
    assert skey_orders is not None

    # --- curva 2..N membros -------------------------------------------
    scaling: dict[str, dict] = {}
    for k in range(2, args.repos + 1):
        ws_name = "scale" if k == args.repos else f"scale-{k}"
        ws = fed.get_workspace(ws_name)
        g = fed.graph(ws, depth=1)
        res = eng.search(SearchRequest(query="orders"), ws.id)
        scaling[str(k)] = {
            "resolve_view": _time(
                lambda ws=ws: fed.resolve_view(ws), rep),
            "entity_lookup": _time(
                lambda ws=ws: fed.entity_occurrences(ws, skey_orders),
                rep),
            "graph_depth1": _time(
                lambda ws=ws: fed.graph(ws, depth=1), rep),
            "search_all_members": _time(
                lambda ws=ws: eng.search(
                    SearchRequest(query="orders"), ws.id), rep),
            "graph_nodes": len(g.nodes),
            "graph_edges": len(g.edges),
            "search_items": len(res.items),
            "search_domain_items": len(res.domain_items),
            "members_searched": res.members_searched,
        }

    # --- 1 vs N dentro do mesmo workspace ------------------------------
    ws = fed.get_workspace("scale")
    res_one = eng.search(
        SearchRequest(query="orders"), ws.id,
        repository_id=repos[0])
    res_sym = eng.search(SearchRequest(query="shared_handler"), ws.id)
    one_vs_n = {
        "search_one_member": _time(
            lambda: eng.search(
                SearchRequest(query="orders"), ws.id,
                repository_id=repos[0]), rep),
        "search_all_members": _time(
            lambda: eng.search(SearchRequest(query="orders"), ws.id),
            rep),
        "search_symbols_all_members": _time(
            lambda: eng.search(
                SearchRequest(query="shared_handler"), ws.id), rep),
        "symbol_items_all_members": len(res_sym.items),
        "one_member_items": len(res_one.items),
        "one_member_domain_items": len(res_one.domain_items),
        "members_searched_one": res_one.members_searched,
        "members_searched_all": args.repos,
    }

    # --- isolamento por pin sob stress --------------------------------
    root_0 = workdir / "repo_0"
    (root_0 / "grow.sql").write_text("SELECT * FROM mart.grown;\n")
    snap0_new = int(
        _cli(["index", str(root_0), "--domains", "sql,api", "--json"])
        ["snapshot_id"])
    view_pinned = fed.resolve_view(ws, {repos[0]: snaps[0]})
    view_latest = fed.resolve_view(ws)
    res_pinned = eng.search(
        SearchRequest(query="grown"), ws.id, pins={repos[0]: snaps[0]})
    res_latest = eng.search(SearchRequest(query="grown"), ws.id)
    leaks = [
        h for h in res_pinned.domain_items
        if h.repository_id == repos[0] and h.snapshot_id != snaps[0]
    ] + [
        h for h in res_pinned.items
        if h.repository_id == repos[0] and h.snapshot_id != snaps[0]
    ]
    found_new = [
        h for h in res_latest.domain_items
        if h.repository_id == repos[0] and h.snapshot_id == snap0_new
    ]
    isolation = {
        "pinned_snapshot": snaps[0],
        "latest_snapshot": snap0_new,
        "pinned_view_id": view_pinned.view_id,
        "latest_view_id": view_latest.view_id,
        "view_id_differs": view_pinned.view_id != view_latest.view_id,
        "pinned_s2_rows": len(leaks),
        "latest_found_s2": len(found_new) > 0,
    }
    if leaks or view_pinned.view_id == view_latest.view_id:
        return {
            "tool": "bench_federation_scale",
            "spec": "lf-0134",
            "isolation": isolation,
            "FAILED": "snapshot isolation violated",
        }

    return {
        "tool": "bench_federation_scale",
        "spec": "lf-0134",
        "seed": SEED,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "repos": args.repos,
        "repeats": args.repeats,
        "setup_s": setup_s,
        "scaling_members": scaling,
        "one_vs_n": one_vs_n,
        "isolation": isolation,
        "notes": [
            "fixtures pequenos e determinísticos (seed fixo) — o regime "
            "medido é o fan-out N-way, não o tamanho do índice",
            "view_id cobre membership + identity_key + pins + digest de "
            "analyzer_versions + resolver federado + schema_version",
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
