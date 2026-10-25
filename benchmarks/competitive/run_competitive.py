#!/usr/bin/env python3
r"""Cycle 8 lf-0143 — competitive benchmark honesto (medido, nunca inventado).

Compara `code-atlas` com a alternativa local realmente instalada —
**ripgrep 14.x** (`rg`) — sobre o corpus real `benchmarks/.corpus/realworld`
(jaffle_shop, terraform-aws-s3-bucket, requests, click, axios; SHAs do
manifest verificados). Ferramentas não instaladas (ast-grep, semgrep,
code2flow, ctags) e SaaS (Sourcegraph, Greptile, Cody) ficam
**NOT RUN** — declarado, não simulado.

Asymmetry explícita (nunca escondida no número):
- `rg` varre **conteúdo de arquivo** a cada query, sem índice — custo
  por query ∝ bytes do corpus; retorna linhas de texto.
- `code-atlas search` consulta **símbolos/arquivos indexados** em
  SQLite — exige `code-atlas index` antes (custo medido aqui);
  retorna entidades tipadas com proveniência. Não busca conteúdo
  arbitrário — quem quer "find text anywhere" é `rg`, ponto.
- Por isso medimos as DUAS latências de code-atlas: (a) CLI
  end-to-end (subprocesso — inclui ~150ms de boot do interpretador,
  que é o que o usuário sente) e (b) engine-only in-process (a conexão
  SQLite já aberta — o que vale num daemon/IDE/API).

Medições por repo (repeats pós-warmup, median/min/p95/max):
1. `rg -i --count-matches "<term>" <repo>` — baseline de scan.
2. `code-atlas search "<term>" --root <repo> --json` — CLI real.
3. Engine in-process: structural / lexical (FTS5) / hybrid (RRF k=60).
4. Tarefa "listar definições": `rg "^\s*(async )?def "` (contagem de
   linhas) vs `search "" --kind function --language python` (símbolos
   tipados) — counts lado a lado com o gap semântico declarado.
5. Tarefa "referências": `rg -i --count-matches "\b<Name>\b"` vs
   `code-atlas references <qname>` — counts + mix de resolution_status
   lado a lado. Alvo = símbolo com mais arestas entrantes (descoberto
   no próprio SQLite — determinístico).
6. Modo daemon: `python -m code_atlas api --port 0` sobre o workspace
   compartilhado → latência client-side de `/api/v1/search` por repo
   (o modo comparável a IDE/daemon, sem boot por query).
7. Capacidades sem baseline local: `code-atlas context` pack,
   `domains lineage` (jaffle_shop), federação (create/add x5/view +
   busca federada in-process) — medidos onde rodam, NOT RUN onde não.
8. Break-even: T_atlas(N)=index_wall+N·q_cli vs T_rg(N)=N·q_rg.

Guardrails (spec lf-0143):
- NUNCA executa código dos repos indexados — só análise estática.
- Segredos não indexados (scanner exclui SECRET_PATTERNS).
- Isolamento: CODE_ATLAS_DATA_DIR/CONFIG_DIR temporários, workspace
  multi-repo compartilhado entre os 5 (modo real de uso + federação);
  PYTHONHASHSEED=0. Nada é escrito dentro do corpus.
- Corpus gitignored; resultado em benchmarks/results/competitive-latest.json.

Uso:
    .venv/bin/python benchmarks/competitive/run_competitive.py
    .venv/bin/python benchmarks/competitive/run_competitive.py --repeats 25
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
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
DEFAULT_MANIFEST = (
    REPO_ROOT / "benchmarks" / "realworld" / "manifest.json")
DEFAULT_OUT = REPO_ROOT / "benchmarks" / "results" / "competitive-latest.json"
ATLAS_BIN = REPO_ROOT / ".venv" / "bin" / "code-atlas"
ATLAS_PY = REPO_ROOT / ".venv" / "bin" / "python"

INDEX_TIMEOUT_S = 900
QUERY_TIMEOUT_S = 60
API_REQ_TIMEOUT_S = 15
API_BOOT_TIMEOUT_S = 30
REPEATS_DEFAULT = 15          # por query subprocesso (rg + CLI atlas)
ENGINE_REPEATS = 60           # in-process é barato — amostra maior
API_REPEATS = 25

# termos de busca por repo: 1º = manifest search_term, 2º = termo extra.
EXTRA_TERMS: dict[str, list[str]] = {
    "jaffle_shop": ["order"],
    "terraform-aws-s3-bucket": ["s3"],
    "requests": ["redirect"],
    "click": ["Context"],
    "axios": ["Axios"],
}

NOT_RUN_TOOLS = {
    "ast-grep": "não instalado; spec proíbe instalar heavyweight — "
                "sem número inventado",
    "semgrep": "não instalado (CLI + engine pesados) — NOT RUN",
    "code2flow/pyan": "não instalado — NOT RUN",
    "ctags/gtags": "não instalado — NOT RUN",
    "Sourcegraph/SCIP": "SaaS/servidor — sem artefato local comparável "
                        "no corpus — NOT RUN (matrix-only)",
    "Greptile/Cody": "SaaS com LLM — fora do escopo local/offline — "
                     "NOT RUN (matrix-only)",
    "understand/scitools": "GUI comercial fechado — NOT RUN",
    "code-atlas impact": "corpus é clone --depth 1 (1 commit) — "
                         "impact exige base+target; homologado em "
                         "cycle-5/5.1 — NOT RUN aqui",
}


def _env(data_dir: Path, config_dir: Path) -> dict[str, str]:
    """Env isolado do subprocesso — nunca herda config do usuário."""
    env = os.environ.copy()
    env["CODE_ATLAS_DATA_DIR"] = str(data_dir)
    env["CODE_ATLAS_CONFIG_DIR"] = str(config_dir)
    env["PYTHONHASHSEED"] = "0"
    return env


def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, round(q * (len(sorted_vals) - 1))))
    return sorted_vals[idx]


def _stats_ms(samples_s: list[float]) -> dict:
    """ms stats + amostras verbatim (transparência, não só resumo)."""
    s = sorted(samples_s)
    return {
        "repeats": len(s),
        "min_ms": round(s[0] * 1000, 3),
        "p50_ms": round(_percentile(s, 0.50) * 1000, 3),
        "p95_ms": round(_percentile(s, 0.95) * 1000, 3),
        "max_ms": round(s[-1] * 1000, 3),
        "samples_ms_verbatim": [round(v * 1000, 3) for v in samples_s],
    }


def _timed_subprocess(
    cmd: list[str], *, env: dict[str, str] | None = None,
    timeout: float = QUERY_TIMEOUT_S, cwd: Path | None = None,
) -> tuple[float, subprocess.CompletedProcess]:
    t0 = time.monotonic()
    proc = subprocess.run(
        cmd, env=env, capture_output=True, text=True,
        timeout=timeout, cwd=cwd)
    return time.monotonic() - t0, proc


def _repeat_subprocess(
    cmd: list[str], repeats: int, *, warmup: int = 2,
    env: dict[str, str] | None = None,
    timeout: float = QUERY_TIMEOUT_S, cwd: Path | None = None,
) -> dict:
    """warmup fora da medição + N amostras de wall do subprocesso."""
    for _ in range(warmup):
        _timed_subprocess(cmd, env=env, timeout=timeout, cwd=cwd)
    samples: list[float] = []
    last: subprocess.CompletedProcess | None = None
    for _ in range(repeats):
        dt, last = _timed_subprocess(
            cmd, env=env, timeout=timeout, cwd=cwd)
        samples.append(dt)
    out = _stats_ms(samples)
    out["cmd"] = " ".join(cmd)
    if last is not None:
        out["exit_code"] = last.returncode
        out["stdout_bytes"] = len(last.stdout or "")
        if (last.stdout or "").strip():
            out["stdout_tail"] = last.stdout.strip()[-300:]
        if last.stderr.strip():
            out["stderr_tail"] = last.stderr.strip()[:300]
    return out


def _repeat_inprocess(fn, repeats: int, *, warmup: int = 3) -> dict:
    """Mede chamada in-process (engine já construído, db aberto)."""
    for _ in range(warmup):
        fn()
    samples: list[float] = []
    last_ret = None
    for _ in range(repeats):
        t0 = time.monotonic()
        last_ret = fn()
        samples.append(time.monotonic() - t0)
    out = _stats_ms(samples)
    return out, last_ret


# -- rg helpers ---------------------------------------------------------

def rg_file_count(repo_path: Path, *, hidden: bool = False) -> int:
    """Arquivos que rg vê — escopo honesto.

    Default (``hidden=False``): rg ignora dotfiles (``.github/`` etc.) —
    menos arquivos que o inventário do Atlas. ``--hidden`` inclui
    dotfiles mas ainda respeita .gitignore; Atlas segue o contrário:
    cobre dotfiles e poda ignore-dirs próprios + segredos (rg varre
    ``.pem/.key`` que Atlas se recusa a indexar). Nenhum default é
    subconjunto do outro — os dois counts são reportados."""
    cmd = ["rg", "--files"]
    if hidden:
        cmd.append("--hidden")
    cmd.append(str(repo_path))
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=QUERY_TIMEOUT_S)
    return len([ln for ln in proc.stdout.splitlines() if ln.strip()])


def rg_count_matches(repo_path: Path, pattern: str,
                     extra: list[str] | None = None) -> int:
    cmd = ["rg", "-i", "--hidden", "--count-matches", pattern]
    if extra:
        cmd += extra
    cmd.append(str(repo_path))
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=QUERY_TIMEOUT_S)
    total = 0
    for line in proc.stdout.splitlines():
        m = re.search(r":(\d+)$", line)
        if m:
            total += int(m.group(1))
    return total


# -- atlas helpers ------------------------------------------------------

def index_repo(repo_path: Path, env: dict[str, str], *,
               full: bool, domains: bool) -> dict:
    cmd = [str(ATLAS_BIN), "index", str(repo_path), "--json"]
    if full:
        cmd.append("--full")
    if domains:
        cmd += ["--domains", "all"]
    t0 = time.monotonic()
    proc = subprocess.run(
        cmd, env=env, capture_output=True, text=True,
        timeout=INDEX_TIMEOUT_S)
    wall_s = time.monotonic() - t0
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:
        return {"cmd": " ".join(cmd), "exit_code": proc.returncode,
                "wall_s": round(wall_s, 3), "status": "parse_error",
                "stdout_tail": proc.stdout[-300:],
                "stderr_tail": proc.stderr[-300:]}
    return {
        "cmd": " ".join(cmd), "exit_code": proc.returncode,
        "wall_s": round(wall_s, 3), "status": payload.get("status"),
        "snapshot_id": payload.get("snapshot_id"),
        "files_indexed": payload.get("files_indexed"),
        "secrets_skipped": payload.get("secrets_skipped"),
        "noop": payload.get("noop"), "error": payload.get("error"),
        "files_parsed": (payload.get("stats") or {}).get("files_parsed"),
        "files_reused": (payload.get("stats") or {}).get("files_reused"),
        "occurrences": (payload.get("stats") or {}).get("occurrences"),
        "relations": (payload.get("stats") or {}).get("relations"),
        "domain_entities": (payload.get("stats") or {}).get(
            "domain_entities") or sum(
            v for k, v in (payload.get("stats") or {}).items()
            if k.endswith("_entities") and k.startswith("domain_")),
        "stage_ms": {k[len("stage_ms_"):]: v
                     for k, v in (payload.get("stats") or {}).items()
                     if k.startswith("stage_ms_")},
    }


def open_workspace_db(data_dir: Path) -> sqlite3.Connection:
    """Mesma conexão do CLI (`_open_db` = connect + migrate).

    Read-write porque é o que o CLI real abre — e porque ``hybrid``
    persiste o cache de embeddings na primeira chamada (custo real,
    medido em ``hybrid_cold_first_call_ms``)."""
    from code_atlas.storage.db import connect
    from code_atlas.storage.migrations import migrate
    conn = connect(data_dir / "atlas.db")
    migrate(conn)
    return conn


def resolve_repo_snapshot(conn, repo_path: Path):
    from code_atlas.search.structural import StructuralSearch
    search = StructuralSearch(conn)
    repo_id = search.repo_for_root(str(repo_path))
    if repo_id is None:
        return None, None, "no indexed repository"
    snap, err = search.resolve_snapshot(repo_id, None)
    if snap is None or snap.id is None:
        return repo_id, None, err or "no READY snapshot"
    return repo_id, int(snap.id), ""


def pick_reference_target(conn, snapshot_id: int,
                          prefer_suffix: str | None) -> dict | None:
    """Símbolo com mais arestas entrantes resolvidas — determinístico.

    prefer_suffix casa o fim do qualified_name (ex.: ``.sessions.Session``)
    quando queremos um alvo conhecido; sem match cai no mais referenciado.
    """
    rows = conn.execute(
        "SELECT s.qualified_name AS qn, COUNT(*) AS c "
        "FROM relations r "
        "JOIN symbol_occurrences o ON o.id = r.to_id "
        "JOIN symbols s ON s.id = o.logical_symbol_id "
        "WHERE r.snapshot_id = ? AND r.to_id IS NOT NULL "
        "AND r.type IN ('REFERENCES','CALLS') "
        "GROUP BY s.id ORDER BY c DESC, qn LIMIT 50",
        (snapshot_id,)).fetchall()
    if not rows:
        return None
    if prefer_suffix:
        for r in rows:
            if str(r["qn"]).endswith(prefer_suffix):
                return {"qualified_name": str(r["qn"]),
                        "incoming_relations": int(r["c"]),
                        "picked": "preferred_suffix"}
    r = rows[0]
    return {"qualified_name": str(r["qn"]),
            "incoming_relations": int(r["c"]),
            "picked": "top_incoming_edges"}


def engine_query_battery(conn, repo_path: Path, repo_id: int,
                         snap_id: int, terms: list[str]) -> dict:
    """Latência in-process das 3 estratégias + counts de hits."""
    from code_atlas.search.contracts import SearchRequest
    from code_atlas.search.engine import SearchEngine
    from code_atlas.search.structural import SearchFilters, StructuralSearch

    structural = StructuralSearch(conn)
    engine = SearchEngine(conn)
    out: dict[str, dict] = {}
    # custo frio real da primeira chamada hybrid — constrói o cache de
    # embeddings naïve do snapshot (escrito no db do workspace).
    t0 = time.monotonic()
    engine.search(SearchRequest(
        query=terms[0], strategy="hybrid",
        repository_root=str(repo_path), limit=20))
    out["_hybrid_cold_first_call_ms"] = round(
        (time.monotonic() - t0) * 1000, 3)
    for term in terms:
        per: dict[str, dict] = {}
        st, ret = _repeat_inprocess(
            lambda t=term: structural.search_symbols(
                repo_id, snap_id, t, SearchFilters(), 50, 0),
            ENGINE_REPEATS)
        st["result_total"] = getattr(ret, "total", None)
        per["structural"] = st
        for strat in ("lexical", "hybrid"):
            st, ret = _repeat_inprocess(
                lambda t=term, s=strat: engine.search(
                    SearchRequest(query=t, strategy=s,
                                  repository_root=str(repo_path),
                                  limit=20)),
                ENGINE_REPEATS)
            st["result_items"] = len(getattr(ret, "items", ()) or ())
            st["diagnostics"] = [
                d.code for d in getattr(ret, "diagnostics", ()) or ()]
            per[strat] = st
        out[term] = per
    return out


def engine_definitions_task(conn, repo_id: int, snap_id: int,
                            language: str | None) -> dict:
    """"Listar definições" — `search "" --kind function` tipado."""
    from code_atlas.search.structural import SearchFilters, StructuralSearch
    structural = StructuralSearch(conn)
    st, ret = _repeat_inprocess(
        lambda: structural.search_symbols(
            repo_id, snap_id, "",
            SearchFilters(language=language, kind="function"), 500, 0),
        ENGINE_REPEATS)
    st["result_total"] = getattr(ret, "total", None)
    return st


def atlas_cli_query(env: dict[str, str], repo_path: Path,
                    term: str, repeats: int) -> dict:
    cmd = [str(ATLAS_BIN), "search", term, "--root", str(repo_path),
           "--json"]
    return _repeat_subprocess(cmd, repeats, env=env)


def atlas_cli_simple(env: dict[str, str], args: list[str],
                     repeats: int = 3, warmup: int = 1,
                     cwd: Path | None = None) -> dict:
    """Chamadas CLI de capability/health. ``warmup=0`` obrigatório para
    comandos que MUTAM estado (federation create/add) — um warmup
    executaria a mutação e a medição mediria o erro idempotente."""
    cmd = [str(ATLAS_BIN), *args]
    return _repeat_subprocess(cmd, repeats, warmup=warmup, env=env,
                              cwd=cwd)


def rg_scan(env_unused, repo_path: Path, term: str,
            repeats: int) -> dict:
    cmd = ["rg", "-i", "--hidden", "--count-matches", term,
           str(repo_path)]
    out = _repeat_subprocess(cmd, repeats)
    out["total_matches"] = rg_count_matches(repo_path, term)
    return out


# -- API / federation ----------------------------------------------------

def _wait_url(proc: subprocess.Popen, timeout: float) -> str:
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
    for tok in line.split():
        if tok.startswith("http://"):
            return tok.strip()
    raise RuntimeError(f"linha de boot inesperada do servidor: {line!r}")


def _timed_get(url: str) -> tuple[float, int, int]:
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(
                url, timeout=API_REQ_TIMEOUT_S) as resp:
            body = resp.read()
            return time.monotonic() - t0, resp.status, len(body)
    except urllib.error.HTTPError as e:
        body = e.read()
        return time.monotonic() - t0, e.code, len(body)


def api_search_latency(env: dict[str, str], repo_terms: dict[int, str],
                       log: list[str]) -> dict:
    """Sobe `code_atlas api --port 0` no workspace e mede /api/v1/search."""
    cmd = [str(ATLAS_PY), "-u", "-m", "code_atlas", "api", "--port", "0"]
    proc = subprocess.Popen(
        cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, cwd=REPO_ROOT)
    try:
        base = _wait_url(proc, API_BOOT_TIMEOUT_S)
        results: dict[str, dict] = {}
        for rid, term in repo_terms.items():
            url = (f"{base}/search?q={urllib.request.quote(term)}"
                   f"&repository_id={rid}&limit=20")
            for _ in range(3):
                _timed_get(url)
            samples, status, nbytes = [], 0, 0
            for _ in range(API_REPEATS):
                dt, status, nbytes = _timed_get(url)
                samples.append(dt)
            stats = _stats_ms(samples)
            stats.update({"http_status": status,
                          "response_bytes": nbytes, "q": term})
            results[str(rid)] = stats
        return {"base_url": base, "cmd": " ".join(cmd),
                "endpoints": results}
    except Exception as exc:
        log.append(f"api falhou: {type(exc).__name__}: {exc}")
        return {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
            log.append("API server teve de ser morto (kill -9)")


def federation_demo(conn, env: dict[str, str], repo_paths: dict[str, str],
                    log: list[str]) -> dict:
    """Cria workspace, adiciona os 5 repos, mede view + busca federada."""
    out: dict = {"workspace": "competitive-corpus"}
    r = atlas_cli_simple(env, ["federation", "create", out["workspace"],
                               "--json"], repeats=1, warmup=0)
    out["create"] = {"exit_code": r.get("exit_code"),
                     "stdout_tail": r.get("stdout_tail")}
    if r.get("exit_code") != 0:
        out["error"] = "federation create falhou"
        out["stderr"] = r.get("stderr_tail")
        return out
    add_codes: dict[str, int | None] = {}
    for name, rpath in repo_paths.items():
        r = atlas_cli_simple(
            env, ["federation", "add", out["workspace"], rpath, "--json"],
            repeats=1, warmup=0)
        add_codes[name] = r.get("exit_code")
    out["add_exit_codes"] = add_codes
    proc = subprocess.run(
        [str(ATLAS_BIN), "federation", "view", out["workspace"], "--json"],
        env=env, capture_output=True, text=True, timeout=QUERY_TIMEOUT_S)
    out["view"] = {"exit_code": proc.returncode}
    try:
        payload = json.loads(proc.stdout)
        out["view"]["members"] = len(payload.get("members") or [])
        out["view"]["member_names"] = [
            m.get("repository") for m in payload.get("members") or []]
    except Exception as exc:
        out["view"]["parse_error"] = f"{type(exc).__name__}: {exc}"
        log.append(f"federation view parse falhou: {exc}")
    # descarta snapshot de leitura potencialmente velho — o WAL read txn
    # desta conn pode anteceder os writes dos subprocessos add/create.
    conn.rollback()
    # busca federada in-process sobre a view (sem CLI por membro)
    try:
        from code_atlas.search.contracts import SearchRequest
        from code_atlas.search.federated import FederatedSearchEngine
        fed = FederatedSearchEngine(conn)
        st, ret = _repeat_inprocess(
            lambda: fed.search(
                SearchRequest(query="session", strategy="hybrid",
                              repository_root="", limit=20),
                out["workspace"]),
            ENGINE_REPEATS)
        st["members_searched"] = getattr(ret, "members_searched", None)
        st["result_items"] = len(getattr(ret, "items", ()) or ())
        out["federated_search_session"] = st
    except Exception as exc:
        out["federated_search_session"] = {
            "error": f"{type(exc).__name__}: {exc}"}
        log.append(f"federated search falhou: {exc}")
    return out


def cache_persistence_probe(env: dict[str, str],
                            repo_path: Path | None) -> dict | str:
    """Probe: `SqliteEmbeddingCache.put` faz INSERT sem commit; o CLI fecha
    a conexão sem commit() → rollback. Mede N chamadas CLI e conta linhas
    na tabela — o resultado observado (0) mostra que toda chamada CLI
    repaga o build do cache naive."""
    if repo_path is None:
        return "NOT RUN — requests ausente"
    out: dict = {}
    rpath = str(repo_path)
    out["hybrid_cli_calls"] = _repeat_subprocess(
        [str(ATLAS_BIN), "search", "session", "--strategy", "hybrid",
         "--root", rpath, "--json"], 3, env=env)
    out["lexical_cli_calls"] = _repeat_subprocess(
        [str(ATLAS_BIN), "search", "session", "--strategy", "lexical",
         "--root", rpath, "--json"], 3, env=env)
    db_path = Path(env["CODE_ATLAS_DATA_DIR"]) / "atlas.db"
    c2 = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        out["embedding_cache_rows_after_cli_calls"] = int(
            c2.execute(
                "SELECT COUNT(*) FROM embedding_cache").fetchone()[0])
    except sqlite3.Error as exc:
        out["embedding_cache_rows_after_cli_calls"] = f"error: {exc}"
    c2.close()
    return out


# -- fase 2 por-repo --------------------------------------------------------

def measure_repo_queries(
    conn: sqlite3.Connection, args: argparse.Namespace,
    env: dict[str, str], entry: dict, repo_path: Path, repo_id: int,
    snap_id: int, terms: list[str],
) -> dict:
    """Bateria de queries de um repo: engine in-process → rollback →
    subprocessos (rg + CLI). Separação rígida: a txn implícita das
    escritas do engine (embedding cache) é descartada ANTES de qualquer
    subprocesso — single-writer WAL; o CLI real nunca commita essas
    writes (rollback em close())."""
    from code_atlas.search.structural import StructuralSearch

    q: dict = {"rg_scan": {}, "atlas_cli_search": {}, "atlas_engine": {}}
    q["atlas_engine"] = engine_query_battery(
        conn, repo_path, repo_id, snap_id, terms)

    # tarefa: listar definições — só onde existe linguagem parseada
    parsed = (entry["index_full_domains"].get("files_parsed") or 0)
    if parsed > 0:
        lang = ("python" if entry["id"] in ("requests", "click")
                else "typescript")
        q["defs_task"] = {
            "language": lang,
            "atlas_engine_search_kind_function": engine_definitions_task(
                conn, repo_id, snap_id, lang),
        }
    else:
        q["defs_task"] = ("NOT RUN — nenhuma linguagem parseada "
                          "no repo (file-nodes only)")

    # tarefa: referências — símbolo com mais arestas entrantes
    pref = {"requests": ".sessions.Session",
            "click": ".core.Command"}.get(entry["id"])
    target = pick_reference_target(conn, snap_id, pref)
    if target is None:
        q["refs_task"] = ("NOT RUN — zero arestas resolvidas "
                          "no snapshot (sem símbolos tipados)")
    else:
        q["refs_task"] = {"target": target}
        qname = target["qualified_name"]
        st, ret = _repeat_inprocess(
            lambda qn=qname: StructuralSearch(conn).references(
                repo_id, snap_id, qn, None, 500, 0),
            ENGINE_REPEATS)
        page = ret[0] if isinstance(ret, tuple) else ret
        st["result_total"] = getattr(page, "total", None)
        status_mix: dict[str, int] = {}
        for item in getattr(page, "items", ()) or ():
            k = str(item.get("resolution_status"))
            status_mix[k] = status_mix.get(k, 0) + 1
        st["resolution_status_mix_first_page"] = status_mix
        q["refs_task"]["atlas_engine"] = st

    # fim das medições in-process
    conn.rollback()

    for term in terms:
        q["rg_scan"][term] = rg_scan(None, repo_path, term, args.repeats)
        q["atlas_cli_search"][term] = atlas_cli_query(
            env, repo_path, term, args.repeats)

    if isinstance(q.get("defs_task"), dict):
        lang = q["defs_task"]["language"]
        if lang == "python":
            rg_def_cmd = ["rg", "-c", "--hidden", "--type", "py",
                          r"^\s*(async\s+)?def\s+\w+", str(repo_path)]
        else:
            rg_def_cmd = ["rg", "-c", "--hidden", "--type", "ts",
                          r"\bfunction\b|=>", str(repo_path)]
        rg_res = _repeat_subprocess(rg_def_cmd, args.repeats)
        proc = subprocess.run(
            rg_def_cmd, capture_output=True, text=True,
            timeout=QUERY_TIMEOUT_S)
        rg_res["matched_line_count"] = sum(
            int(ln.rsplit(":", 1)[-1])
            for ln in proc.stdout.splitlines() if ln.strip())
        q["defs_task"]["rg"] = rg_res

    if isinstance(q.get("refs_task"), dict):
        qname = q["refs_task"]["target"]["qualified_name"]
        bare = qname.rsplit(".", 1)[-1]
        q["refs_task"]["rg_word_scan"] = rg_scan(
            None, repo_path, rf"\b{re.escape(bare)}\b", args.repeats)
        q["refs_task"]["atlas_cli_references"] = _repeat_subprocess(
            [str(ATLAS_BIN), "references", qname, "--root",
             str(repo_path), "--json"], args.repeats, env=env)

    # context pack — capacidade sem baseline rg
    q["context_pack"] = atlas_cli_simple(
        env, ["context", terms[0], "--level", "focus", "--budget",
              "2000", "--root", str(repo_path), "--json"], repeats=5)
    return q


# -- main ----------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--repeats", type=int, default=REPEATS_DEFAULT)
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args()

    manifest = json.loads(args.manifest.read_text())
    repos = [r for r in manifest["repos"]
             if args.only is None or r["id"] in args.only]
    if not repos:
        print("nenhum repo casou --only", file=sys.stderr)
        return 2

    work = Path(tempfile.mkdtemp(prefix="atlas-competitive-"))
    data_dir, config_dir = work / "data", work / "config"
    data_dir.mkdir()
    config_dir.mkdir()
    env = _env(data_dir, config_dir)
    log: list[str] = []
    results: list[dict] = []
    t_all = time.monotonic()

    # -- fase 0: startup floors ------------------------------------------
    print("[bench] startup floors...", flush=True)
    startup = {
        "rg_version": _repeat_subprocess(["rg", "--version"], 10),
        "atlas_version": _repeat_subprocess(
            [str(ATLAS_BIN), "--version"], 10, env=env),
    }
    print(f"  rg --version p50={startup['rg_version']['p50_ms']}ms · "
          f"code-atlas --version p50={startup['atlas_version']['p50_ms']}ms",
          flush=True)

    # -- fase 1: index compartilhado (workspace multi-repo real) ---------
    for spec in repos:
        repo_path = args.corpus / spec["id"]
        entry: dict = {"id": spec["id"], "url": spec["url"],
                       "sha_expected": spec["sha"], "shape": spec["shape"]}
        if not repo_path.is_dir():
            entry["error"] = "corpus ausente — rode clone_corpus.sh"
            results.append(entry)
            log.append(f"{spec['id']}: corpus ausente")
            continue
        sha = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            capture_output=True, text=True).stdout.strip()
        entry["sha_actual"] = sha
        entry["sha_match"] = sha == spec["sha"]
        entry["rg_files_default"] = rg_file_count(repo_path)
        entry["rg_files_hidden"] = rg_file_count(repo_path, hidden=True)

        print(f"[bench] {spec['id']} index --full --domains all...",
              flush=True)
        entry["index_full_domains"] = index_repo(
            repo_path, env, full=True, domains=True)

        # incremental DEVE usar a mesma pipeline (--domains all) — flags
        # diferentes mudam o plano e legitimamente produzem snapshot novo.
        print(f"[bench] {spec['id']} index incremental (noop esperado)...",
              flush=True)
        entry["index_incremental"] = index_repo(
            repo_path, env, full=False, domains=True)

        results.append(entry)

    # -- fase 2: queries ---------------------------------------------------
    conn = open_workspace_db(data_dir)
    repo_ids: dict[str, int] = {}
    repo_terms_api: dict[int, str] = {}
    try:
        for entry in results:
            if "index_full_domains" not in entry:
                entry["queries"] = "NOT RUN — sem índice"
                continue
            repo_path = args.corpus / entry["id"]
            repo_id, snap_id, err = resolve_repo_snapshot(conn, repo_path)
            if repo_id is None or snap_id is None:
                entry["queries"] = f"NOT RUN — {err}"
                log.append(f"{entry['id']}: {err}")
                continue
            repo_ids[entry["id"]] = repo_id
            entry["repo_id"] = repo_id
            entry["snapshot_id"] = snap_id
            manifest_term = next(
                (r["search_term"] for r in repos if r["id"] == entry["id"]),
                "")
            terms = [t for t in
                     [manifest_term, *EXTRA_TERMS.get(entry["id"], [])]
                     if t]
            entry["terms"] = terms
            repo_terms_api[repo_id] = terms[0]

            print(f"[bench] {entry['id']} queries "
                  f"(rg/CLI/engine)...", flush=True)
            entry["queries"] = measure_repo_queries(
                conn, args, env, entry, repo_path, repo_id, snap_id,
                terms)
    finally:
        conn.close()

    # -- fase 3: daemon mode (API) ----------------------------------------
    print("[bench] api-mode search latency...", flush=True)
    api = api_search_latency(env, repo_terms_api, log)

    # -- fase 3.5: probe — embedding_cache persiste via CLI? --------------
    req_path = next(
        (args.corpus / e["id"] for e in results
         if e.get("id") == "requests" and "repo_id" in e), None)
    cache_probe = cache_persistence_probe(env, req_path)

    # -- fase 4: federação + capacidades sem baseline ----------------------
    print("[bench] federation + capability probes...", flush=True)
    repo_paths = {e["id"]: str(args.corpus / e["id"]) for e in results
                  if "repo_id" in e}
    conn = open_workspace_db(data_dir)
    try:
        fed = federation_demo(conn, env, repo_paths, log)
        # domains lineage — jaffle_shop (dbt project, adapter sql)
        lineage = "NOT RUN"
        if "jaffle_shop" in repo_ids:
            ent = conn.execute(
                "SELECT identity_key FROM domain_entities "
                "WHERE repository_id = ? ORDER BY identity_key LIMIT 20",
                (repo_ids["jaffle_shop"],)).fetchall()
            keys = [str(r["identity_key"]) for r in ent]
            # prefere entidade do adapter sql (linhagem de modelos dbt)
            target_key = next((k for k in keys if k.startswith("dom:sql/")),
                              next((k for k in keys if "customer" in k),
                                   keys[0] if keys else None))
            if target_key:
                lineage = {
                    "entity_key": target_key,
                    "cli": atlas_cli_simple(
                        env, ["domains", "lineage", target_key,
                              "--root",
                              str(args.corpus / "jaffle_shop"),
                              "--direction", "both", "--json"],
                        repeats=5),
                    "candidates": keys,
                }
            else:
                lineage = "NOT RUN — zero domain_entities em jaffle_shop"
        mcp_doctor = atlas_cli_simple(env, ["mcp", "doctor", "--json"],
                                      repeats=3)
    finally:
        conn.close()

    payload = {
        "benchmark": "competitive",
        "spec": "lf-0143",
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_wall_s": round(time.monotonic() - t_all, 3),
        "env": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
            "rg_version": startup["rg_version"].get("stderr_tail") or
                subprocess.run(["rg", "--version"], capture_output=True,
                               text=True).stdout.splitlines()[0],
            "atlas_bin": str(ATLAS_BIN),
            "code_atlas_version": subprocess.run(
                [str(ATLAS_BIN), "--version"], capture_output=True,
                text=True, timeout=15).stdout.strip(),
        },
        "startup_floors": startup,
        "not_run": NOT_RUN_TOOLS,
        "method": {
            "isolation": "CODE_ATLAS_DATA_DIR/CONFIG_DIR temporários; "
                         "UM workspace multi-repo compartilhado (modo "
                         "real de uso; habilita federação); "
                         "PYTHONHASHSEED=0; nada escrito no corpus",
            "index": "code-atlas index <repo> --full --domains all "
                     "--json (superset: símbolos + domínio opt-in)",
            "rg": "rg -i --count-matches <term> — scan de conteúdo "
                  "por query, sem índice; respeita .gitignore",
            "atlas_cli": "code-atlas search <term> --root <repo> --json "
                         "— subprocesso completo (boot Python incluso)",
            "atlas_engine": "chamada in-process StructuralSearch/"
                            "SearchEngine sobre conexão read-only "
                            "aberta — sem boot, o modo daemon/IDE",
            "api": "python -u -m code_atlas api --port 0; 3 warmup + "
                   f"{API_REPEATS} GETs /api/v1/search por repo",
            "warmup": "2 subprocesso / 3 in-process fora da medição",
        },
        "asymmetry_note": "rg busca CONTEÚDO de arquivo (linhas); "
                          "code-atlas busca SÍMBOLOS/ARQUIVOS indexados "
                          "— classes de query diferentes. Números lado "
                          "a lado, nunca como equivalentes funcionais.",
        "guardrails": {
            "executes_indexed_repo_code": False,
            "secrets_indexed": False,
            "corpus_untouched": True,
            "bounded": {"index_timeout_s": INDEX_TIMEOUT_S,
                        "query_timeout_s": QUERY_TIMEOUT_S},
        },
        "repos": results,
        "api_mode": api,
        "federation": fed,
        "domains_lineage_jaffle": lineage,
        "mcp_doctor": mcp_doctor,
        "embedding_cache_persistence_probe": cache_probe,
        "runner_log": log,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"[done] {args.out} ({len(results)} repos, "
          f"{payload['total_wall_s']}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
