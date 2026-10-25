#!/usr/bin/env python3
"""Cycle 8 lf-0128 — real-world corpus benchmark (medido, nunca inventado).

Para cada repo do corpus (`manifest.json`, clones em ``benchmarks/.corpus/``):

- ``code-atlas index <repo> --full --json`` — wall time do subprocesso real +
  breakdown ``stats.stage_ms_*`` do próprio coordinator;
- ``code-atlas index <repo> --full --domains all --json`` — delta de custo dos
  adapters de domínio opt-in (wall + contagens ``domain_*``);
- contagens por snapshot direto no SQLite (file_inventory, symbol_occurrences,
  relations, evidence, domain_entities, domain_relations) — verdade do banco,
  não só o payload do CLI;
- latência de ``/api/v1/graph/overview``, ``/api/v1/search`` e
  ``/api/v1/graph/neighbors`` contra um servidor real
  (``python -u -m code_atlas api --port 0``).

Guardrails (spec):
- NUNCA executa código dos repos indexados — só ``code-atlas index`` (análise
  estática). Nenhum hook/teste/script do corpus roda.
- Segredos nunca são indexados — o scanner já exclui ``SECRET_PATTERNS``
  (.pem/.key/.env…); ``secrets_skipped`` é registrado por repo.
- Tempo limitado: timeout por comando; repeats de API limitados.
- Isolamento: ``CODE_ATLAS_DATA_DIR``/``CODE_ATLAS_CONFIG_DIR`` apontam para
  dirs temporários por modo — o config do usuário (resolvers opt-in, retenção)
  não contamina o baseline, que fica syntactic-only + adapters conforme flag.
- Corpus em ``benchmarks/.corpus/`` (gitignored); resultados em
  ``benchmarks/results/realworld-latest.json``.

Uso:
    .venv/bin/python benchmarks/realworld/run_benchmark.py
    .venv/bin/python benchmarks/realworld/run_benchmark.py --repeats 10 --keep-data
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = REPO_ROOT / "benchmarks" / ".corpus" / "realworld"
DEFAULT_MANIFEST = Path(__file__).resolve().parent / "manifest.json"
DEFAULT_OUT = REPO_ROOT / "benchmarks" / "results" / "realworld-latest.json"
ATLAS_BIN = REPO_ROOT / ".venv" / "bin" / "code-atlas"
ATLAS_PY = REPO_ROOT / ".venv" / "bin" / "python"

INDEX_TIMEOUT_S = 900          # teto por `code-atlas index`
API_REQ_TIMEOUT_S = 15         # teto por request HTTP
API_BOOT_TIMEOUT_S = 30        # teto para o servidor imprimir a URL
API_REPEATS_DEFAULT = 25       # requests por endpoint (após warmup)
# tabela → coluna de escopo do count (snapshot-scoped salvo domain_entities,
# que é repo-scoped por design — identidade estável entre snapshots).
TABLE_SCOPES = (
    ("file_inventory", "snapshot_id"),
    ("symbol_occurrences", "snapshot_id"),
    ("relations", "snapshot_id"),
    ("evidence", "snapshot_id"),
    ("domain_entities", "repository_id"),
    ("domain_entity_occurrences", "snapshot_id"),
    ("domain_relations", "snapshot_id"),
)


def _env(data_dir: Path, config_dir: Path) -> dict[str, str]:
    """Env isolado do subprocesso — nunca herda config do usuário."""
    env = os.environ.copy()
    env["CODE_ATLAS_DATA_DIR"] = str(data_dir)
    env["CODE_ATLAS_CONFIG_DIR"] = str(config_dir)
    env["PYTHONHASHSEED"] = "0"  # variância de hash-order fora da medição
    return env


def run_index(repo_path: Path, work_dir: Path, *, domains: bool,
              log: list[str]) -> dict:
    """Roda `code-atlas index` real (subprocesso) e mede wall time."""
    data_dir = work_dir / "data"
    config_dir = work_dir / "config"
    data_dir.mkdir(parents=True)
    config_dir.mkdir(parents=True)
    cmd = [str(ATLAS_BIN), "index", str(repo_path), "--full", "--json"]
    if domains:
        cmd += ["--domains", "all"]
    t0 = time.monotonic()
    proc = subprocess.run(
        cmd, env=_env(data_dir, config_dir), capture_output=True,
        text=True, timeout=INDEX_TIMEOUT_S)
    wall_s = time.monotonic() - t0
    if proc.stderr.strip():
        log.append(f"{repo_path.name}: stderr: {proc.stderr.strip()[:400]}")
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    stats = payload.get("stats") or {}
    stage_ms = {k[len("stage_ms_"):]: v for k, v in stats.items()
                if k.startswith("stage_ms_")}
    return {
        "cmd": " ".join(cmd),
        "exit_code": proc.returncode,
        "wall_s": round(wall_s, 3),
        "status": payload.get("status"),
        "snapshot_id": payload.get("snapshot_id"),
        "files_indexed": payload.get("files_indexed"),
        "secrets_skipped": payload.get("secrets_skipped"),
        "noop": payload.get("noop"),
        "error": payload.get("error"),
        "warnings": payload.get("warnings"),
        "diagnostics": payload.get("diagnostics"),
        "domains_enabled": payload.get("domains"),
        "stats": {k: v for k, v in stats.items()
                  if not k.startswith("stage_ms_")},
        "stage_ms": stage_ms,
        "data_dir": str(data_dir),
        "db_path": str(data_dir / "atlas.db"),
    }


def db_snapshot_counts(db_path: Path, snapshot_id: int,
                       repository_id: int) -> dict[str, int]:
    """Contagens por escopo lidas do banco (read-only) — fonte da verdade.

    Tabelas snapshot-scoped usam ``snapshot_id``; ``domain_entities`` é
    repo-scoped (schema: identidade estável entre snapshots) e usa
    ``repository_id``.
    """
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        out: dict[str, int] = {}
        existing = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for table, col in TABLE_SCOPES:
            if table not in existing:
                continue
            scope = repository_id if col == "repository_id" else snapshot_id
            out[table] = int(conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {col} = ?",
                (scope,)).fetchone()[0])
        out["symbols"] = int(conn.execute(
            "SELECT COUNT(DISTINCT logical_symbol_id) FROM symbol_occurrences "
            "WHERE snapshot_id = ?", (snapshot_id,)).fetchone()[0])
        return out
    finally:
        conn.close()


def _pick_neighbor_occurrence(db_path: Path, snapshot_id: int) -> int | None:
    """occurrence_id do nó com mais arestas saíntes (determinístico)."""
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        row = conn.execute(
            "SELECT from_id, COUNT(*) AS c FROM relations "
            "WHERE snapshot_id = ? AND from_id IS NOT NULL "
            "GROUP BY from_id ORDER BY c DESC, from_id LIMIT 1",
            (snapshot_id,)).fetchone()
        if row:
            return int(row[0])
        row = conn.execute(
            "SELECT id FROM symbol_occurrences WHERE snapshot_id = ? "
            "ORDER BY id LIMIT 1", (snapshot_id,)).fetchone()
        return int(row[0]) if row else None
    finally:
        conn.close()


def _wait_url(proc: subprocess.Popen, timeout: float) -> str:
    """Lê a 1ª linha do servidor (`API Explorer em <url>`) com teto real."""
    import select

    deadline = time.monotonic() + timeout
    assert proc.stdout is not None
    line = ""
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"servidor API morreu no boot: {proc.stderr.read()[:300]}")
        ready, _, _ = select.select([proc.stdout], [], [], 0.25)
        if ready:
            line = proc.stdout.readline()
            if line:
                break
    if not line:
        raise RuntimeError("timeout esperando URL do servidor API")
    # formato: "API Explorer em http://127.0.0.1:PORT/api/v1 — ..."
    for tok in line.split():
        if tok.startswith("http://"):
            return tok.strip()
    raise RuntimeError(f"linha de boot inesperada do servidor: {line!r}")


def _timed_get(url: str) -> tuple[float, int, int]:
    """GET simples → (latência_s, http_status, bytes). Sem retry."""
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(
                url, timeout=API_REQ_TIMEOUT_S) as resp:
            body = resp.read()
            return time.monotonic() - t0, resp.status, len(body)
    except urllib.error.HTTPError as e:
        body = e.read()
        return time.monotonic() - t0, e.code, len(body)


def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, round(q * (len(sorted_vals) - 1))))
    return sorted_vals[idx]


def api_latency(db_path: Path, repo_id: str, search_term: str,
                neighbor_occ: int | None, repeats: int,
                log: list[str]) -> dict:
    """Sobe `code-atlas api` real e mede latência dos 3 endpoints da spec."""
    config_dir = db_path.parent.parent / "config"
    env = os.environ.copy()
    env["CODE_ATLAS_DATA_DIR"] = str(db_path.parent)
    env["CODE_ATLAS_CONFIG_DIR"] = str(config_dir)
    env["PYTHONHASHSEED"] = "0"
    cmd = [str(ATLAS_PY), "-u", "-m", "code_atlas", "api", "--port", "0"]
    proc = subprocess.Popen(
        cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, cwd=REPO_ROOT)
    try:
        base = _wait_url(proc, API_BOOT_TIMEOUT_S)
        endpoints: dict[str, str] = {
            "/graph/overview": f"{base}/graph/overview?repository_id={repo_id}",
            "/search": f"{base}/search?q={search_term}"
                       f"&repository_id={repo_id}",
        }
        if neighbor_occ is not None:
            endpoints["/graph/neighbors"] = (
                f"{base}/graph/neighbors?occurrence_id={neighbor_occ}"
                f"&repository_id={repo_id}")
        results: dict[str, dict] = {}
        for name, url in endpoints.items():
            # warmup fora da medição (JIT/imports/conexão)
            for _ in range(3):
                _timed_get(url)
            samples: list[float] = []
            status = 0
            body_bytes = 0
            hits: dict | int | None = None
            for _ in range(repeats):
                dt, status, body_bytes = _timed_get(url)
                samples.append(dt)
            if name == "/search" and status == 200:
                with urllib.request.urlopen(
                        url, timeout=API_REQ_TIMEOUT_S) as resp:
                    payload = json.loads(resp.read())
                hits = {
                    "items": len(payload.get("items") or []),
                    "domain_items": len(payload.get("domain_items") or []),
                }
            if name == "/graph/neighbors" and status == 200:
                with urllib.request.urlopen(
                        url, timeout=API_REQ_TIMEOUT_S) as resp:
                    payload = json.loads(resp.read())
                hits = len(payload.get("edges") or [])
            s = sorted(samples)
            results[name] = {
                "requests": repeats,
                "http_status": status,
                "response_bytes": body_bytes,
                "result_items": hits,
                "min_ms": round(s[0] * 1000, 3),
                "p50_ms": round(_percentile(s, 0.50) * 1000, 3),
                "p95_ms": round(_percentile(s, 0.95) * 1000, 3),
                "max_ms": round(s[-1] * 1000, 3),
            }
        if neighbor_occ is None:
            results["/graph/neighbors"] = {
                "skipped": "sem symbol_occurrences no snapshot — "
                           "repo sem arquivos em linguagens parseáveis "
                           "(.py/.ts)"}
        return {"base_url": base, "cmd": " ".join(cmd),
                "endpoints": results}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
            log.append(f"{repo_id}: API server teve de ser morto (kill -9)")


def corpus_stats(repo_path: Path) -> dict:
    """Inventário do corpus — arquivos, bytes, histograma de extensão."""
    files = 0
    total_bytes = 0
    hist: dict[str, int] = {}
    for p in repo_path.rglob("*"):
        if not p.is_file() or ".git" in p.parts:
            continue
        files += 1
        total_bytes += p.stat().st_size
        ext = p.suffix.lower() or "<none>"
        hist[ext] = hist.get(ext, 0) + 1
    top = dict(sorted(hist.items(), key=lambda kv: -kv[1])[:10])
    return {"files": files, "bytes": total_bytes, "ext_top10": top}


def repo_db_id(db_path: Path) -> int | None:
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        row = conn.execute("SELECT id FROM repositories ORDER BY id LIMIT 1"
                           ).fetchone()
        return int(row[0]) if row else None
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--repeats", type=int, default=API_REPEATS_DEFAULT)
    ap.add_argument("--keep-data", action="store_true",
                    help="mantém os data dirs (default: apaga ao final)")
    ap.add_argument("--only", nargs="*", default=None,
                    help="roda só os ids listados")
    args = ap.parse_args()

    manifest = json.loads(args.manifest.read_text())
    repos = [r for r in manifest["repos"]
             if args.only is None or r["id"] in args.only]
    if not repos:
        print("nenhum repo no manifest casou --only", file=sys.stderr)
        return 2

    run_tmp = Path(tempfile.mkdtemp(prefix="atlas-realworld-"))
    log: list[str] = []
    results: list[dict] = []
    t_all = time.monotonic()
    try:
        for spec in repos:
            repo_path = args.corpus / spec["id"]
            entry: dict = {
                "id": spec["id"], "url": spec["url"],
                "sha_expected": spec["sha"], "shape": spec["shape"],
            }
            if not repo_path.is_dir():
                entry["error"] = f"corpus ausente: {repo_path} — rode "
                entry["error"] += "benchmarks/realworld/clone_corpus.sh"
                entry["index_default"] = "NOT RUN"
                entry["index_domains_all"] = "NOT RUN"
                entry["api"] = "NOT RUN"
                results.append(entry)
                log.append(f"{spec['id']}: NOT RUN — corpus ausente")
                continue
            sha = subprocess.run(
                ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
                capture_output=True, text=True, check=True).stdout.strip()
            entry["sha_actual"] = sha
            entry["sha_match"] = sha == spec["sha"]
            entry["corpus"] = corpus_stats(repo_path)

            print(f"[bench] {spec['id']} index(default)...", flush=True)
            work = run_tmp / spec["id"]
            default = run_index(repo_path, work / "default",
                                domains=False, log=log)
            default.pop("data_dir")
            snap_default = default.get("snapshot_id")
            rid_d = repo_db_id(Path(default["db_path"]))
            if default.get("status") == "success" and snap_default and rid_d:
                default["db_counts"] = db_snapshot_counts(
                    Path(default["db_path"]), int(snap_default), rid_d)
                default["db_bytes"] = Path(default["db_path"]).stat().st_size
            entry["index_default"] = default

            print(f"[bench] {spec['id']} index(--domains all)...", flush=True)
            doms = run_index(repo_path, work / "domains",
                             domains=True, log=log)
            doms.pop("data_dir")
            snap_doms = doms.get("snapshot_id")
            rid = repo_db_id(Path(doms["db_path"]))
            if doms.get("status") == "success" and snap_doms and rid:
                doms["db_counts"] = db_snapshot_counts(
                    Path(doms["db_path"]), int(snap_doms), rid)
                doms["db_bytes"] = Path(doms["db_path"]).stat().st_size
            entry["index_domains_all"] = doms
            entry["domains_delta"] = {
                "wall_s": round(
                    (doms.get("wall_s") or 0) - (default.get("wall_s") or 0), 3),
                "domain_entities": (doms.get("db_counts") or {}).get(
                    "domain_entities", 0),
                "domain_relations": (doms.get("db_counts") or {}).get(
                    "domain_relations", 0),
            }

            # API: serve o banco do modo --domains all (snapshot mais rico).
            db_path = Path(doms["db_path"])
            if rid is not None and snap_doms:
                occ = _pick_neighbor_occurrence(db_path, int(snap_doms))
                print(f"[bench] {spec['id']} api latency...", flush=True)
                try:
                    entry["api"] = api_latency(
                        db_path, str(rid), spec["search_term"], occ,
                        args.repeats, log)
                except Exception as exc:  # honesto: mede ou declara
                    entry["api"] = {"error": f"{type(exc).__name__}: {exc}"}
                    log.append(f"{spec['id']}: api falhou: {exc}")
            else:
                entry["api"] = "NOT RUN — sem snapshot READY para servir"
            results.append(entry)
    finally:
        if not args.keep_data:
            shutil.rmtree(run_tmp, ignore_errors=True)

    payload = {
        "benchmark": "realworld",
        "spec": "lf-0128",
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_wall_s": round(time.monotonic() - t_all, 3),
        "env": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
            "atlas_bin": str(ATLAS_BIN),
            "code_atlas_version": _atlas_version(log),
        },
        "method": {
            "index": "subprocesso `code-atlas index <repo> --full --json` "
                     "com CODE_ATLAS_DATA_DIR/CONFIG_DIR isolados e "
                     "PYTHONHASHSEED=0; stage_ms_* vem do coordinator",
            "api": "subprocesso `python -u -m code_atlas api --port 0` "
                   "sobre o db do modo --domains all; 3 warmups + N "
                   "requests por endpoint, latência client-side",
            "counts": "contagens por snapshot lidas do SQLite (mode=ro), "
                      "não só do payload do CLI",
        },
        "guardrails": {
            "executes_indexed_repo_code": False,
            "secrets_indexed": False,
            "secrets_note": "SECRET_PATTERNS do scanner excluídos antes de "
                            "hash/inventory; secrets_skipped registrado",
            "bounded": {"index_timeout_s": INDEX_TIMEOUT_S,
                        "api_req_timeout_s": API_REQ_TIMEOUT_S},
            "isolation": "data/config dirs temporários por repo/modo",
        },
        "repos": results,
        "runner_log": log,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"[done] {args.out} ({len(results)} repos, "
          f"{payload['total_wall_s']}s)")
    return 0


def _atlas_version(log: list[str]) -> str:
    try:
        out = subprocess.run([str(ATLAS_BIN), "--version"],
                             capture_output=True, text=True, timeout=15)
        return out.stdout.strip() or out.stderr.strip()
    except Exception as exc:
        log.append(f"--version falhou: {exc}")
        return "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
