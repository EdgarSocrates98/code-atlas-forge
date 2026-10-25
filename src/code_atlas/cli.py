"""Interface de linha de comando do Code Atlas.

Cycle 1: ``--version``, ``doctor``, ``init``, ``index``, ``status``.
Códigos de saída consistentes:

- 0: sucesso
- 1: erro de execução (inclui índice falho)
- 2: erro de uso (argparse)
- 3: bloqueado/unsafe (path inválido, config ausente para ação exigida)

``--json`` emite um único objeto JSON em stdout; erros vão para stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from code_atlas.domains.crossresolve import CrossDomainResolver
    from code_atlas.domains.registry import DomainRegistry
    from code_atlas.federation import Federation

from code_atlas import __version__, gitbin
from code_atlas.config import (
    ConfigError,
    config_file,
    data_dir,
    resolve_root,
    write_config,
)
from code_atlas.discovery.identity import identity_key_for_root
from code_atlas.discovery.scanner import probe_git
from code_atlas.indexing import IndexCoordinator, IndexOutcome
from code_atlas.search.structural import (
    DEFAULT_LIMIT,
    MAX_DEPTH,
    MAX_LIMIT,
    SCHEMA_VERSION,
    SearchFilters,
    StructuralSearch,
    clamp_limit,
)
from code_atlas.storage import connect, migrate, schema_version
from code_atlas.storage.store import Store

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_UNSAFE = 3

DB_FILENAME = "atlas.db"


def db_path() -> Path:
    """Banco do workspace (fora do repositório indexado)."""
    return data_dir() / DB_FILENAME


def _open_db() -> sqlite3.Connection:
    conn = connect(db_path())
    migrate(conn)
    return conn


def _resolver_factories(
    target: Path,
    *,
    scip_path: str | None = None,
    only: Sequence[str] | None = None,
) -> tuple[Sequence[Any], list[str], dict[str, str], int]:
    """Resolvers opt-in + ``retain_snapshots`` do ``config.toml``.

    ``[resolvers]`` (lf-0027): ausência de config/tabela → zero
    factories (syntactic only). Erros de bootstrap são devolvidos
    como strings de diagnóstico — nunca levantam.
    ``[indexing] retain_snapshots`` (lf-0031): 0 = ilimitado.
    lf-0032: ``scip_path`` sobrescreve ``[resolvers.scip].index_path``;
    ``only`` restringe os resolvers ativos a ``scip`` / ``lsp`` /
    ``lsp:<nome>`` (``syntactic`` é sempre o baseline).
    """
    from code_atlas.resolvers.bootstrap import resolver_factories

    cfg_path = config_file()
    resolvers: Any = None
    retain = 0
    errors: list[str] = []
    if cfg_path.exists():
        from code_atlas.config import WorkspaceConfig

        try:
            cfg = WorkspaceConfig.load(cfg_path)
            resolvers = cfg.resolvers
            retain = cfg.retain_snapshots
        except ConfigError as exc:
            return (), [f"config load failed: {exc}"], {}, 0
    table: dict[str, Any] = dict(resolvers or {})
    if scip_path:
        scip = dict(table.get("scip") or {})
        scip["index_path"] = scip_path
        table["scip"] = scip
    if only:
        names = {n.strip() for n in only if n.strip()}
        known_lsp = set(
            ((resolvers or {}).get("lsp", {}) or {}).get("servers", {})
            or {}
        ) if isinstance((resolvers or {}).get("lsp"), dict) else set()
        unknown = sorted(
            n for n in names
            if n not in ("syntactic", "scip", "lsp")
            and not (n.startswith("lsp:") and n[4:] in known_lsp)
        )
        for u in unknown:
            errors.append(f"--resolver: resolver desconhecido '{u}'")
        if "scip" not in names:
            table.pop("scip", None)
        lsp = table.get("lsp")
        if isinstance(lsp, dict) and "lsp" not in names:
            wanted = {n[4:] for n in names if n.startswith("lsp:")}
            servers = dict(lsp.get("servers") or {})
            if wanted:
                lsp = dict(lsp)
                lsp["servers"] = {
                    k: v for k, v in servers.items() if k in wanted
                }
                table["lsp"] = lsp
            else:
                table.pop("lsp", None)
    versions: dict[str, str] = {}
    factories, boot_errors = resolver_factories(table or None, target, versions)
    errors.extend(boot_errors)
    return tuple(factories), errors, versions, retain


@dataclass(frozen=True)
class Check:
    """Uma verificação do ``doctor``."""

    name: str
    value: str
    ok: bool
    detail: str = ""


def _probe_git() -> Check:
    if gitbin.git_binary() is None:
        return Check("git", "absent", True, "git binary not found; degraded discovery mode")
    version = gitbin.git_version()
    if not version:
        return Check("git", "error", True, "git --version failed")
    return Check("git", version, True)


def _probe_fts5() -> bool:
    try:
        con = sqlite3.connect(":memory:")
        try:
            con.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        finally:
            con.close()
        return True
    except sqlite3.Error:
        return False


def _first_writable_ancestor(path: Path) -> tuple[Path, bool]:
    """Primeiro ancestral existente de ``path`` e se é gravável."""
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current, current.exists() and os.access(current, os.W_OK)


def run_doctor(args: argparse.Namespace) -> int:
    """Executa verificações reais de ambiente. Sai 0 se não houver falhas."""
    checks: list[Check] = []

    py_ok = sys.version_info >= (3, 12)
    checks.append(
        Check(
            "python",
            sys.version.split()[0],
            py_ok,
            "" if py_ok else "requires >= 3.12",
        )
    )
    checks.append(Check("sqlite", sqlite3.sqlite_version, True))
    checks.append(Check("fts5", "available" if _probe_fts5() else "unavailable", _probe_fts5()))
    checks.append(_probe_git())

    cfg = config_file()
    checks.append(
        Check(
            "config",
            str(cfg),
            True,
            "exists" if cfg.exists() else "missing (run `code-atlas init`)",
        )
    )
    dd = data_dir()
    ancestor, writable = _first_writable_ancestor(dd)
    checks.append(
        Check(
            "data_dir",
            str(dd),
            writable,
            f"writable via {ancestor}" if writable else f"not writable (checked {ancestor})",
        )
    )

    failed = [c for c in checks if not c.ok]
    if args.json:
        payload: dict[str, Any] = {
            "tool": "code-atlas",
            "version": __version__,
            "healthy": not failed,
            "checks": [c.__dict__ for c in checks],
        }
        print(json.dumps(payload, indent=2))
    else:
        width = max(len(c.name) for c in checks)
        for c in checks:
            mark = "ok" if c.ok else "FAIL"
            suffix = f"  {c.detail}" if c.detail else ""
            print(f"{c.name:<{width}}  {mark:<4}  {c.value}{suffix}")
        if failed:
            print(f"\n{len(failed)} check(s) failed", file=sys.stderr)
    return EXIT_ERROR if failed else EXIT_OK


def run_init(args: argparse.Namespace) -> int:
    """Cria config, data dir e registra o repositório no índice local."""
    root = resolve_root(args.root)
    if not root.is_dir():
        print(f"error: root {root} is not a directory", file=sys.stderr)
        return EXIT_UNSAFE
    try:
        cfg = write_config(root, force=args.force)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    conn = _open_db()
    try:
        # lf-0021: identidade lógica via probe git — sem scan completo.
        repo_id = Store(conn).get_or_create_repository(
            identity_key_for_root(root), str(root)
        )
        conn.commit()
    finally:
        conn.close()
    payload = {
        "config": str(cfg),
        "root": str(root),
        "data_dir": str(data_dir()),
        "database": str(db_path()),
        "repository_id": repo_id,
    }
    if args.json:
        print(json.dumps(payload))
    else:
        for k, v in payload.items():
            print(f"{k}: {v}")
    return EXIT_OK


def run_status(args: argparse.Namespace) -> int:
    """Relata estado do índice para a raiz resolvida (funciona pré-index)."""
    root = resolve_root(args.root)
    cfg = config_file()
    db_file = db_path()
    initialized = db_file.exists()

    payload: dict[str, object] = {
        "tool": "code-atlas",
        "version": __version__,
        "root": str(root),
        "config": str(cfg),
        "config_exists": cfg.exists(),
        "database": str(db_file),
        "initialized": initialized,
    }
    git_info = probe_git(root) if root.is_dir() else None
    git_payload: dict[str, object] | None = None
    if git_info is not None:
        git_payload = {
            "available": git_info.available,
            "is_repo": git_info.is_repo,
            "branch": git_info.branch,
            "head_sha": git_info.head_sha,
            "dirty": git_info.dirty,
            "is_worktree": git_info.is_worktree,
        }
    payload["git"] = git_payload

    if not initialized:
        payload["snapshot"] = None
        payload["counts"] = {"source_files": 0, "snapshots": 0, "index_jobs": 0}
        payload["schema_version"] = 0
        if not args.json:
            print(f"root: {root}")
            print("state: not initialized — run `code-atlas init` or `code-atlas index`")
            if git_info and git_info.is_repo:
                print(f"git: {git_info.branch or 'detached'}@{git_info.head_sha[:12]}")
        else:
            print(json.dumps(payload))
        return EXIT_OK

    conn = _open_db()
    try:
        store = Store(conn)
        payload["schema_version"] = schema_version(conn)
        row = conn.execute(
            "SELECT id FROM repositories WHERE root_handle = ?", (str(root),)
        ).fetchone()
        repo_id: int | None = int(row["id"]) if row else None
        payload["repository_id"] = repo_id
        count_queries = {
            "source_files": "SELECT COUNT(*) AS c FROM source_files",
            "snapshots": "SELECT COUNT(*) AS c FROM snapshots",
            "index_jobs": "SELECT COUNT(*) AS c FROM index_jobs",
        }
        payload["counts"] = {
            name: int(conn.execute(sql).fetchone()["c"])
            for name, sql in count_queries.items()
        }
        snap = store.latest_ready_snapshot(repo_id) if repo_id else None
        snap_payload: dict[str, object] | None = None
        if snap is not None:
            snap_payload = {
                "id": snap.id,
                "status": str(snap.status),
                "worktree_digest": snap.worktree_digest,
                "git_ref": snap.git_ref,
                "commit_sha": snap.commit_sha,
                "parent_snapshot_id": snap.parent_snapshot_id,
                "created_at": snap.created_at,
                "files": store.inventory_count(snap.id) if snap.id else 0,
            }
        payload["snapshot"] = snap_payload
    finally:
        conn.close()

    if args.json:
        print(json.dumps(payload))
    else:
        print(f"root:       {root}")
        print(f"schema:     v{payload['schema_version']}")
        if git_payload:
            print(f"git:        {git_payload['branch'] or 'detached'}"
                  f"@{str(git_payload['head_sha'])[:12]} "
                  f"{'(dirty)' if git_payload['dirty'] else '(clean)'}")
        if snap_payload:
            print(f"snapshot:   #{snap_payload['id']} {snap_payload['status']}"
                  f" — {snap_payload['files']} files")
        else:
            print("snapshot:   none (run `code-atlas index`)")
    return EXIT_OK


def run_index(args: argparse.Namespace) -> int:
    """Indexa o inventário do diretório-alvo via IndexCoordinator.

    Path arg com segmento ``..`` é rejeitado (hardening): para indexar um
    diretório pai, passe o caminho absoluto diretamente.
    """
    if args.path and ".." in Path(args.path).parts:
        print("error: '..' traversal is not allowed in PATH", file=sys.stderr)
        return EXIT_UNSAFE
    target = Path(args.path).expanduser().resolve() if args.path else resolve_root(args.root)
    if target == target.parent:
        print("error: refusing to index filesystem root", file=sys.stderr)
        return EXIT_UNSAFE
    if not target.is_dir():
        print(f"error: {target} is not a directory", file=sys.stderr)
        return EXIT_UNSAFE
    conn = _open_db()
    try:
        # Resolvers opt-in (lf-0027): [resolvers] do config.toml —
        # erros de bootstrap viram diagnóstico no payload, não crash.
        # lf-0032: --scip sobrescreve o index_path; --resolver restringe
        # os resolvers ativos — nome desconhecido é erro tipado (nunca
        # uma flag que silenciosamente não faz nada).
        factories, boot_errors, res_versions, retain, fatal = (
            _index_resolvers(target, args))
        if fatal:
            conn.close()
            return _emit_error(args, fatal)
        # Domínio opt-in (lf-0123, Cycle 7 Phase 15): ``--domains``
        # monta o registry de adapters built-in; ``--cross-domain``
        # injeta o resolver. Default = flags ausentes → ``None`` →
        # comportamento byte a byte anterior (adapters OFF).
        dom_reg, cross_res, dom_err = _index_domains(target, args)
        if dom_err:
            conn.close()
            return _emit_error(args, dom_err)
        coord = IndexCoordinator(
            conn, resolver_factories=factories,
            resolver_versions=res_versions,
            snapshot_retention=retain,
            domain_registry=dom_reg,
            cross_resolver=cross_res)
        # lf-0022: incremental é o default — ``--full`` é o escape
        # (force desliga o caminho incremental internamente).
        # lf-0141: ``--verbose`` liga o callback de progresso — linhas
        # ``[index]`` em stderr, stdout fica livre para ``--json``.
        outcome: IndexOutcome = coord.index(
            target, force=args.full, incremental=True,
            progress=_index_progress
            if getattr(args, "verbose", False) else None)
        plan_counts = (
            coord.last_plan.counts() if coord.last_plan is not None else {}
        )
    finally:
        conn.close()
    payload = {
        "tool": "code-atlas",
        "version": __version__,
        "schema_version": SCHEMA_VERSION,
        "root": str(target),
        "snapshot_id": outcome.snapshot_id,
        "job_id": outcome.job_id,
        "status": outcome.status,
        "files_indexed": outcome.files_indexed,
        "secrets_skipped": outcome.secrets_skipped,
        "warnings": list(outcome.warnings),
        "noop": outcome.noop,
        "error": outcome.error,
        "stats": outcome.stats,
        "plan": plan_counts,
        "diagnostics": boot_errors,
        "domains": (
            sorted(d.domain_id for d in dom_reg.enabled())
            if dom_reg is not None else []
        ),
        "cross_domain": cross_res is not None,
    }
    if args.json:
        print(json.dumps(payload))
    else:
        _print_index_human(outcome)
    return EXIT_OK if outcome.status in ("success", "noop") else EXIT_ERROR


def _index_progress(event: str, fields: Mapping[str, Any]) -> None:
    """``index --verbose``: uma linha ``[index] <evento> k=v …`` em stderr.

    stdout fica reservado ao payload — ``--json`` continua parseável
    com o progresso ligado. Nenhum campo carrega paths nem segredos.
    """
    parts = " ".join(f"{k}={v}" for k, v in fields.items())
    print(f"[index] {event} {parts}".rstrip(), file=sys.stderr)


def _index_domains(
    target: Path, args: argparse.Namespace,
) -> tuple[DomainRegistry | None, CrossDomainResolver | None, str]:
    """Flags de domínio do ``index`` → (registry, cross_resolver, fatal).

    ``--domains`` aceita ids separados por vírgula ou ``all``;
    ``--cross-domain`` exige ``--domains`` (sem rows de domínio o plano
    do resolver sai vazio — recusar é mais honesto que indexar uma
    versão de analisador que não faz nada).
    """
    spec = getattr(args, "domains", None)
    cross = bool(getattr(args, "cross_domain", False))
    if not spec:
        if cross:
            return None, None, "--cross-domain requires --domains"
        return None, None, ""
    from code_atlas.domains.cli_surface import select_domain_registry

    reg, err = select_domain_registry(spec, target)
    if err or reg is None:
        return None, None, err or "--domains: registry build failed"
    resolver = None
    if cross:
        from code_atlas.domains.crossresolve import CrossDomainResolver

        resolver = CrossDomainResolver()
    return reg, resolver, ""


def _print_index_human(outcome: IndexOutcome) -> None:
    if outcome.status == "success":
        print(f"snapshot #{outcome.snapshot_id} READY — "
              f"{outcome.files_indexed} inventory files")
        s = outcome.stats
        if s.get("occurrences") or s.get("relations"):
            print(f"graph: {s.get('symbols_new', 0)} symbols, "
                  f"{s.get('occurrences', 0)} occurrences, "
                  f"{s.get('relations', 0)} relations "
                  f"({s.get('files_failed', 0)} files failed)")
        if s.get("domain_adapters"):
            dom = {k: v for k, v in s.items()
                   if k.startswith("domain_") and k != "domain_adapters"}
            print(f"domains: {s['domain_adapters']} adapter(s) — "
                  + ", ".join(f"{k}={v}" for k, v in sorted(dom.items())))
    elif outcome.status == "noop":
        print(f"snapshot #{outcome.snapshot_id} unchanged — no-op index")
    else:
        print(f"index failed: {outcome.error}", file=sys.stderr)
    if outcome.secrets_skipped:
        print(f"secrets excluded: {outcome.secrets_skipped}")
    for w in outcome.warnings:
        print(f"warning: {w}", file=sys.stderr)


def _index_resolvers(
    target: Path, args: argparse.Namespace,
) -> tuple[Sequence[Any], list[str], dict[str, str], int, str]:
    """Flags de resolver do ``index`` → factories/versions/retain.

    ``fatal`` não-vazio = erro tipado de flag (resolver desconhecido)
    — o caller aborta sem indexar.
    """
    only: list[str] = []
    for r in args.resolver or ():
        only.extend(p for p in r.split(",") if p.strip())
    factories, errors, versions, retain = _resolver_factories(
        target, scip_path=args.scip, only=only or None)
    bad = "; ".join(
        e for e in errors if e.startswith("--resolver:"))
    return factories, errors, versions, retain, bad


def run_semantic_status(args: argparse.Namespace) -> int:
    """lf-0032: resolvers installed/enabled/used + cobertura do READY.

    Lê só o snapshot READY persistido — nunca inventa cobertura.
    ``installed``/``enabled`` vêm de ``describe_resolvers`` (read-only:
    nenhum server LSP é spawnado); ``used`` vem de
    ``relations.resolver`` — a única fonte autoritativa de uso real.
    """
    target = (
        Path(args.path).expanduser().resolve()
        if args.path else resolve_root(args.root)
    )
    conn = _open_db()
    try:
        payload = _semantic_payload(conn, target)
    finally:
        conn.close()
    return _emit_page(args, payload)


def _semantic_payload(
    conn: sqlite3.Connection, target: Path
) -> dict[str, Any]:
    """Monta o payload do ``semantic-status`` lendo só o READY."""
    search = StructuralSearch(conn)
    repo_id = search.repo_for_root(str(target))
    snap = (
        search.resolve_snapshot(repo_id, None)[0]
        if repo_id is not None else None
    )
    coverage, used = _resolver_usage(conn, snap)
    reports, boot_errors = _resolver_reports(target, used)
    analyzer_versions = dict(snap.analyzer_versions) if snap else {}
    last_job: dict[str, Any] = {}
    if repo_id is not None:
        row = conn.execute(
            "SELECT counts FROM index_jobs WHERE repository_id = ? "
            "AND status IN ('success','noop','failed') "
            "ORDER BY id DESC LIMIT 1",
            (repo_id,),
        ).fetchone()
        if row is not None:
            last_job = json.loads(row["counts"] or "{}")
    sem_keys = ("semantic_attempted", "semantic_enriched",
                "semantic_diagnostics")
    payload: dict[str, Any] = {
        "tool": "code-atlas",
        "version": __version__,
        "schema_version": SCHEMA_VERSION,
        "root": str(target),
        "snapshot_id": snap.id if snap else None,
        "indexed": snap is not None,
        "resolvers": reports,
        "coverage": {
            "resolved": coverage.get("resolved", 0),
            "ambiguous": coverage.get("ambiguous", 0),
            "unresolved": coverage.get("unresolved", 0),
            "external": coverage.get("external", 0),
            "total": sum(coverage.values()),
        },
        "semantic": {k: last_job.get(k, 0) for k in sem_keys},
        "revision": {
            "commit_sha": snap.commit_sha if snap else None,
            "scip_index_sha": analyzer_versions.get("resolver:scip"),
        },
        "diagnostics": boot_errors,
        "_human": [],
    }
    for rep in reports:
        flags = "/".join(
            k for k in ("installed", "enabled", "used") if rep[k])
        payload["_human"].append(
            f"{rep['id']}: {flags or 'none'} edges={rep['edges']}")
    cov = payload["coverage"]
    payload["_human"].append(
        f"coverage: resolved={cov['resolved']} "
        f"ambiguous={cov['ambiguous']} "
        f"unresolved={cov['unresolved']} external={cov['external']}")
    for e in boot_errors:
        payload["_human"].append(f"diagnostic: {e}")
    return payload


def _resolver_usage(
    conn: sqlite3.Connection, snap: Any
) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    """Cobertura por status + uso por resolver no snapshot READY."""
    coverage: dict[str, int] = {}
    used: dict[str, dict[str, int]] = {}
    if snap is None or snap.id is None:
        return coverage, used
    for r in conn.execute(
        "SELECT resolver, resolution_status, COUNT(*) AS c "
        "FROM relations WHERE snapshot_id = ? "
        "GROUP BY resolver, resolution_status",
        (snap.id,),
    ):
        coverage[r["resolution_status"]] = (
            coverage.get(r["resolution_status"], 0) + r["c"])
        used.setdefault(r["resolver"], {}).setdefault(
            r["resolution_status"], 0)
        used[r["resolver"]][r["resolution_status"]] += r["c"]
    return coverage, used


def _resolver_reports(
    target: Path, used: dict[str, dict[str, int]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """``describe_resolvers`` + marcação de uso real por resolver."""
    from code_atlas.resolvers.bootstrap import describe_resolvers

    table: Any = None
    cfg_path = config_file()
    if cfg_path.exists():
        from code_atlas.config import WorkspaceConfig

        try:
            table = WorkspaceConfig.load(cfg_path).resolvers
        except ConfigError:
            table = None
    reports, errors = describe_resolvers(table, target)
    for rep in reports:
        rid = str(rep["id"])
        by_status = dict(used.get(rid, {}))
        if rid == "syntactic-scope-v1":
            # arestas sintáticas carregam o parser-id como resolver
            # (ex.: tree-sitter-python) — tudo que não é resolver
            # externo pertence ao tier sintático baseline.
            for k, v in used.items():
                if k == rid:
                    continue
                if not k.startswith("scip") and not k.startswith("lsp:"):
                    for st, c in v.items():
                        by_status[st] = by_status.get(st, 0) + c
        rep["used"] = bool(by_status)
        rep["edges"] = sum(by_status.values())
        rep["by_status"] = by_status
    return reports, errors


def _search_context(
    args: argparse.Namespace,
) -> tuple[
    sqlite3.Connection | None,
    int | None,
    int | None,
    dict[str, Any] | None,
    str,
]:
    """Resolve (conn, repo_id, snapshot, repo_info) para read-only.

    Erros são impressos em stderr + JSON de erro; retorna tupla com
    ``None``s e mensagem quando indisponível — caller sai EXIT_ERROR.
    """
    root = resolve_root(args.root)
    conn = _open_db()
    search = StructuralSearch(conn)
    root_handle = str(root)
    repo_id = search.repo_for_root(root_handle)
    if repo_id is None:
        conn.close()
        return None, None, None, None, f"no indexed repository at {root_handle}"
    snap, err = search.resolve_snapshot(repo_id, args.snapshot)
    if err or snap is None or snap.id is None:
        conn.close()
        return None, None, None, None, err or "snapshot missing id"
    repo_info = search.repository_info(repo_id) or {"id": repo_id}
    return conn, repo_id, snap.id, repo_info, ""


def _emit_error(args: argparse.Namespace, msg: str) -> int:
    if args.json:
        print(json.dumps({"tool": "code-atlas", "version": __version__,
                          "error": msg, "schema_version": SCHEMA_VERSION}))
    else:
        print(f"error: {msg}", file=sys.stderr)
    return EXIT_ERROR


def _emit_page(args: argparse.Namespace, payload: dict[str, Any]) -> int:
    """--json: objeto único parseável. Humano: linhas pré-formatadas."""
    if args.json:
        payload.pop("_human", None)
        print(json.dumps(payload))
    else:
        for line in payload.pop("_human", []):
            print(line)
        if "total" in payload:
            print(
                f"-- total={payload['total']} "
                f"truncated={payload.get('truncated', False)}"
            )
    return EXIT_OK


def run_search(args: argparse.Namespace) -> int:
    conn, repo_id, snap_id, repo_info, err = _search_context(args)
    if err:
        return _emit_error(args, err)
    assert (
        conn is not None and repo_id is not None and snap_id is not None
    )
    strategy = getattr(args, "strategy", "structural") or "structural"
    if strategy != "structural":
        if repo_info is None:
            return _emit_error(args, "repository info unavailable")
        return _run_search_engine(args, conn, snap_id, repo_info, strategy)
    try:
        search = StructuralSearch(conn)
        filters = SearchFilters(
            language=args.language, kind=args.kind, path=args.path_filter,
        )
        limit = clamp_limit(args.limit)
        page = search.search_symbols(
            repo_id, snap_id, args.query, filters, limit, max(0, args.offset)
        )
        results = [dict(x) for x in page.items]
        payload: dict[str, Any] = {
            "tool": "code-atlas", "version": __version__,
            "schema_version": SCHEMA_VERSION, "command": "search",
            "query": args.query, "snapshot_id": snap_id,
            "repository": repo_info,
            "diagnostics": [],
            "filters": {"language": args.language, "kind": args.kind,
                        "path": args.path_filter},
            "limit": limit, "offset": args.offset,
            "total": page.total, "truncated": page.truncated,
            "results": results,
            "_human": [
                f"{r['symbol_id']:>5} {r['kind']:<9} {r['language']:<10} "
                f"{r['qualified_name']}" for r in results
            ],
        }
    finally:
        conn.close()
    return _emit_page(args, payload)


def _run_search_engine(
    args: argparse.Namespace,
    conn: sqlite3.Connection,
    snap_id: int,
    repo_info: dict[str, Any],
    strategy: str,
) -> int:
    """Estratégias do SearchEngine (lexical|graph|hybrid) com envelope v2."""
    from code_atlas.search import SearchEngine, SearchRequest
    from code_atlas.search.contracts import encode_cursor, query_fingerprint

    offset = max(0, args.offset)
    cursor = None
    if offset:
        fp = query_fingerprint(args.query, {
            "language": args.language, "kind": args.kind,
            "path": args.path_filter})
        cursor = encode_cursor(offset, fp)
    try:
        res = SearchEngine(conn).search(
            SearchRequest(
                query=args.query,
                strategy=strategy,
                language=args.language,
                kind=args.kind,
                path=args.path_filter,
                repository_root=str(resolve_root(args.root)),
                snapshot_id=snap_id,
                limit=clamp_limit(args.limit),
                cursor=cursor,
            )
        )
    finally:
        conn.close()
    items = [asdict(h) for h in res.items]
    payload: dict[str, Any] = res.to_dict()
    payload["tool"] = "code-atlas"
    payload["version"] = __version__
    payload["command"] = "search"
    payload["repository"] = repo_info
    payload["_human"] = [
        f"{h['qualified_name']:<40} {h['path']} "
        f"[{','.join(h['matched_fields']) or 'structural'} "
        f"score={h['score']:.4f}]" for h in items
    ]
    return _emit_page(args, payload)


def run_context(args: argparse.Namespace) -> int:
    """`code-atlas context TASK` — pack de contexto sob budget (lf-0040)."""
    conn, _repo_id, snap_id, _info, err = _search_context(args)
    if err:
        return _emit_error(args, err)
    assert conn is not None and snap_id is not None
    from code_atlas.context import (
        ContextEngine,
        ContextRequest,
        render_json,
        render_markdown,
    )

    try:
        pack = ContextEngine(conn).build(ContextRequest(
            task=args.task,
            repository_root=str(resolve_root(args.root)),
            snapshot_id=snap_id,
            level=args.level,
            budget_tokens=args.budget,
            max_symbols=clamp_limit(args.limit),
        ))
    finally:
        conn.close()
    if args.format == "markdown":
        print(render_markdown(pack))
        return EXIT_OK
    if args.json or args.format == "json":
        print(render_json(pack))
        return EXIT_OK
    # humano: linhas resumidas
    m = pack.manifest
    print(f"# pack snapshot={m.snapshot_id} level={m.level} "
          f"selected={m.selected} omitted={m.omitted} "
          f"truncated={str(m.truncated).lower()}")
    for i in pack.items:
        print(f"  [{i.priority}] {i.kind:<10} {i.title} "
              f"(~{i.est_tokens} tok)")
    if pack.diagnostics.codes:
        print(f"diagnostics: {', '.join(pack.diagnostics.codes)}")
    return EXIT_OK


def run_context_expand(args: argparse.Namespace) -> int:
    """`code-atlas context-expand --symbol X` — expansão autocontida."""
    conn, _repo_id, snap_id, _info, err = _search_context(args)
    if err:
        return _emit_error(args, err)
    assert conn is not None and snap_id is not None
    from code_atlas.context import ContextExpander, expansion_to_json

    delivered = frozenset(
        s for s in (args.delivered or "").split(",") if s)
    try:
        res = ContextExpander(conn).expand(
            snap_id, args.symbol, delivered=delivered,
            budget_tokens=args.budget)
    finally:
        conn.close()
    if res.error:
        return _emit_error(args, res.error)
    if args.json:
        print(expansion_to_json(res))
    else:
        for i in res.added:
            print(f"  + {i.symbol_key} (~{i.est_tokens} tok) {i.path}")
        for r in res.remaining_refs:
            print(f"  … {r.ref_id} ({r.reason})")
    return EXIT_OK


_MCP_MANIFESTS: dict[str, dict[str, Any]] = {
    "claude-code": {"mcpServers": {"code-atlas": {
        "command": "code-atlas", "args": ["mcp"]}}},
    "codex-cli": {"mcp_servers": {"code-atlas": {
        "command": "code-atlas", "args": ["mcp"]}}},
    "generic": {"mcpServers": {"code-atlas": {
        "command": "code-atlas", "args": ["mcp"]}}},
}


def run_api(args: argparse.Namespace) -> int:
    """`code-atlas api serve` — API HTTP local loopback (Cycle 6)."""
    from code_atlas.api.server import serve

    db_file = db_path()
    if not db_file.exists():
        print("banco não existe — rode `code-atlas index` antes",
              file=sys.stderr)
        return EXIT_ERROR
    server = serve(str(db_file), host=args.host, port=args.port,
                   verbose=args.verbose)
    print(f"API Explorer em {server.url} — Ctrl+C para parar")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
    return EXIT_OK


def run_explorer(args: argparse.Namespace) -> int:
    """`code-atlas explorer` — serve API + assets compilados do Explorer.

    Nunca abre o navegador sem `--open`; bind sempre loopback; se a dist
    do Angular não existir, serve só a API e avisa.
    """
    from code_atlas.api.server import serve

    db_file = db_path()
    if not db_file.exists():
        print("banco não existe — rode `code-atlas index` antes",
              file=sys.stderr)
        return EXIT_ERROR
    dist = Path(__file__).resolve().parents[2] / "explorer" / "dist" / "explorer" / "browser"
    if not dist.is_dir():
        dist = Path(__file__).resolve().parents[2] / "explorer" / "dist"
    static = dist if (dist / "index.html").is_file() else None
    if static is None:
        print("Explorer não compilado — servindo apenas a API "
              "(build: cd explorer && npm run build)", file=sys.stderr)
    try:
        server = serve(str(db_file), host=args.host, port=args.port,
                       verbose=args.verbose, static_root=static)
    except OSError as e:
        print(f"porta indisponível: {e}", file=sys.stderr)
        return EXIT_ERROR
    url = f"http://{server.host}:{server.port}/"
    print(f"Explorer em {url} — API em {server.url}")
    if args.open:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
    return EXIT_OK


def run_mcp(args: argparse.Namespace) -> int:
    """`code-atlas mcp [doctor|manifest] ` — servidor stdio + host UX.

    `doctor` diagnostica instalação/capabilities; `manifest` EMITE o
    manifest do host (nunca edita config global — só instrui).
    """
    from code_atlas.mcp import main_stdio

    cmd = getattr(args, "cmd", "serve") or "serve"
    if cmd == "manifest":
        host = getattr(args, "host", "generic") or "generic"
        manifest = _MCP_MANIFESTS.get(host, _MCP_MANIFESTS["generic"])
        print(json.dumps(manifest, indent=2))
        print("# cole o JSON acima no config do host — Atlas nunca "
              "escreve nele", file=sys.stderr)
        return EXIT_OK
    if cmd == "doctor":
        return _mcp_doctor(args)
    return main_stdio()


def _mcp_doctor(args: argparse.Namespace) -> int:
    """Diagnóstico de instalação: executável, DB, capabilities, ndjson."""
    import shutil

    from code_atlas.search.vector import sqlite_vec_available
    from code_atlas.storage import schema_version

    exe = shutil.which("code-atlas") or "<não no PATH — use python -m>"
    checks: dict[str, Any] = {
        "executable": exe,
        "python": sys.version.split()[0],
        "db_path": str(db_path()),
        "db_exists": db_path().exists(),
        "sqlite_vec": str(sqlite_vec_available()),
    }
    if checks["db_exists"]:
        conn = _open_db()
        checks["schema_version"] = schema_version(conn)
        conn.close()
    if args.json:
        print(json.dumps({"tool": "code-atlas",
                          "command": "mcp doctor", **checks}))
    else:
        for k, v in checks.items():
            print(f"{k:<16} {v}")
    return EXIT_OK


def run_symbol(args: argparse.Namespace) -> int:
    conn, repo_id, snap_id, repo_info, err = _search_context(args)
    if err:
        return _emit_error(args, err)
    assert (
        conn is not None and repo_id is not None and snap_id is not None
    )
    try:
        search = StructuralSearch(conn)
        detail = search.symbol_detail(repo_id, snap_id, args.ident)
        if detail is None:
            return _emit_error(args, f"symbol {args.ident!r} not found")
        payload: dict[str, Any] = {
            "tool": "code-atlas", "version": __version__,
            "schema_version": SCHEMA_VERSION, "command": "symbol",
            "snapshot_id": snap_id, "result": detail,
            "repository": repo_info, "diagnostics": [],
            "_human": [
                f"{detail['kind']} {detail['qualified_name']} "
                f"({detail['language']})"
            ] + [
                f"  {o['path']}:{o['line']} [{o['parse_status']}]"
                for o in detail["occurrences"]
            ],
        }
    finally:
        conn.close()
    return _emit_page(args, payload)


def run_references(args: argparse.Namespace) -> int:
    conn, repo_id, snap_id, repo_info, err = _search_context(args)
    if err:
        return _emit_error(args, err)
    assert (
        conn is not None and repo_id is not None and snap_id is not None
    )
    try:
        search = StructuralSearch(conn)
        page, rerr = search.references(
            repo_id, snap_id, args.ident, args.relation_type,
            clamp_limit(args.limit), max(0, args.offset),
        )
        if page is None:
            return _emit_error(args, rerr)
        results = [dict(x) for x in page.items]
        payload: dict[str, Any] = {
            "tool": "code-atlas", "version": __version__,
            "schema_version": SCHEMA_VERSION, "command": "references",
            "symbol": args.ident, "snapshot_id": snap_id,
            "repository": repo_info, "diagnostics": [],
            "limit": clamp_limit(args.limit), "offset": args.offset,
            "total": page.total, "truncated": page.truncated,
            "results": results,
            "_human": [
                f"#{r['relation_id']} {r['type']} "
                f"{r['from']['symbol']}@{r['from']['path']}:{r['from']['line']} "
                f"-> {r['to']['symbol'] if r['to'] else r['to_unresolved']} "
                f"[{r['resolution_status']}]"
                for r in results
            ],
        }
    finally:
        conn.close()
    return _emit_page(args, payload)


def run_graph(args: argparse.Namespace) -> int:
    conn, repo_id, snap_id, repo_info, err = _search_context(args)
    if err:
        return _emit_error(args, err)
    assert (
        conn is not None and repo_id is not None and snap_id is not None
    )
    try:
        search = StructuralSearch(conn)
        filters = SearchFilters(
            language=args.language, kind=args.kind,
            path=args.path_filter, relation_type=args.relation_type,
        )
        dump = search.graph_dump(snap_id, filters, clamp_limit(args.limit))
        payload: dict[str, Any] = {
            "tool": "code-atlas", "version": __version__,
            "schema_version": SCHEMA_VERSION, "command": "graph",
            "snapshot_id": snap_id,
            "repository": repo_info, "diagnostics": [], "filters": {
                "language": args.language, "kind": args.kind,
                "path": args.path_filter, "relation_type": args.relation_type,
            },
            "limit": clamp_limit(args.limit),
            **dump,
            "_human": [
                f"nodes={dump['counts']['nodes']} edges={dump['counts']['edges']}",
            ],
        }
    finally:
        conn.close()
    return _emit_page(args, payload)


def run_index_plan(args: argparse.Namespace) -> int:
    """``index-plan`` (lf-0022): dry-run — plano JSON, zero writes no DB."""
    if args.path and ".." in Path(args.path).parts:
        print("error: '..' traversal is not allowed in PATH", file=sys.stderr)
        return EXIT_UNSAFE
    target = (
        Path(args.path).expanduser().resolve()
        if args.path
        else resolve_root(args.root)
    )
    if not target.is_dir():
        print(f"error: {target} is not a directory", file=sys.stderr)
        return EXIT_UNSAFE
    conn = _open_db()
    try:
        plan, diag = IndexCoordinator(conn).plan_dry_run(target)
    finally:
        conn.close()
    assert plan is not None  # dry-run sempre materializa um plano
    payload: dict[str, Any] = {
        "tool": "code-atlas", "version": __version__,
        "schema_version": SCHEMA_VERSION, "command": "index-plan",
        "root": str(target), "diagnostics": list(diag.values()),
        "plan": plan.to_dict(),
    }
    if args.json:
        print(json.dumps(payload))
    else:
        counts = plan.counts()
        print("index-plan (dry-run): " + ", ".join(
            f"{k}={v}" for k, v in sorted(counts.items())
        ))
        if diag:
            for d in diag.values():
                print(f"note: {d}")
        for f in plan.files:
            print(f"  {f.action:<22} {f.path}  ({f.reason})")
    return EXIT_OK


def run_definitions(args: argparse.Namespace) -> int:
    """``definitions`` (lf-0022): sites de definição do símbolo."""
    conn, repo_id, snap_id, repo_info, err = _search_context(args)
    if err:
        return _emit_error(args, err)
    assert (
        conn is not None and repo_id is not None and snap_id is not None
    )
    try:
        search = StructuralSearch(conn)
        out = search.definitions(repo_id, snap_id, args.ident)
        if out is None:
            return _emit_error(args, f"symbol {args.ident!r} not found")
        defs = out["definitions"]
        payload: dict[str, Any] = {
            "tool": "code-atlas", "version": __version__,
            "schema_version": SCHEMA_VERSION, "command": "definitions",
            "query": args.ident, "snapshot_id": snap_id,
            "repository": repo_info, "diagnostics": [],
            "symbol_id": out["symbol_id"],
            "qualified_name": out["qualified_name"],
            "total": len(defs), "truncated": False,
            "results": defs,
            "_human": [
                f"{out['qualified_name']} ({out['kind']})",
                *(
                    f"  {d['path']}:{d['line']}:{d['col']}"
                    f" [{d['parse_status']}]"
                    for d in defs
                ),
            ],
        }
    finally:
        conn.close()
    return _emit_page(args, payload)


def run_impact(args: argparse.Namespace) -> int:
    """``impact`` (Cycle 5): análise de impacto base e target (snapshots)."""
    from code_atlas.impact.contracts import (
        ImpactLimits,
        ImpactRequest,
    )
    from code_atlas.impact.engine import ImpactEngine
    from code_atlas.impact.report import to_json, to_markdown

    root = resolve_root(args.root)
    conn = _open_db()
    try:
        base = args.base or ("HEAD" if args.cmd in ("worktree", "diff")
                             else "")
        req = ImpactRequest(
            repository_root=str(root),
            base_revision=base,
            target_revision=(args.target if args.cmd != "worktree"
                             else ""),
            symbol=args.ident if args.cmd in ("symbol", "contract")
            else None,
            known_only=args.known_only,
            limits=ImpactLimits(
                max_depth=args.depth, max_nodes=args.max_nodes,
                max_paths=args.max_paths))
        res = ImpactEngine(conn).analyze(req)
        if args.cmd == "tests":
            # relatório focado em candidatos a teste
            res = replace(
                res, symbol_changes=(), contract_changes=(),
                impacts=())
        if args.format == "json":
            print(to_json(res))
            return 0
        if args.format == "markdown":
            print(to_markdown(res))
            return 0
        # humano: resumo determinístico
        print(f"impact: {res.base_revision} → {res.target_revision}")
        print(f"  snapshots: {res.base_snapshot_id} → "
              f"{res.target_snapshot_id}")
        print(f"  changes: {len(res.symbol_changes)} símbolo(s), "
              f"{len(res.contract_changes)} contrato(s)")
        print(f"  impacts: {len(res.impacts)} "
              f"(diretos: {sum(1 for i in res.impacts if i.impact.value == 'direct')})")
        print(f"  tests: {len(res.test_candidates)} candidato(s)")
        print(f"  risk: {res.risk.category.value} "
              f"(uncertainty={res.risk.uncertainty})")
        for r in res.risk.reasons:
            print(f"    - {r}")
        for d in res.diagnostics:
            print(f"  diagnostic {d.code}: {d.message}",
                  file=sys.stderr)
        if res.truncated:
            print("  TRUNCATED — limites atingidos")
        return 0
    finally:
        conn.close()


def _fed_cmd_create(fed: Federation, args: argparse.Namespace) -> dict[str, Any]:
    ws = fed.create_workspace(args.name)
    return {"workspace_id": ws.id, "workspace": ws.name, "created": True}


def _fed_cmd_add(fed: Federation, args: argparse.Namespace) -> dict[str, Any]:
    ws = fed.get_workspace(args.name)
    repo_id = fed.resolve_repository(args.repo)
    member = fed.add_member(ws, repo_id)
    return {
        "workspace_id": ws.id, "workspace": ws.name,
        "repository_id": member.repository_id, "member_id": member.id,
    }


def _fed_cmd_pin(fed: Federation, args: argparse.Namespace) -> dict[str, Any]:
    from code_atlas.federation import InvalidPinError

    ws = fed.get_workspace(args.name)
    repo_id = fed.resolve_repository(args.repo)
    if args.snapshot == "latest":
        member = fed.unpin_member(ws, repo_id)
    else:
        try:
            snap_id = int(args.snapshot)
        except ValueError as exc:
            raise InvalidPinError(
                f"snapshot must be an id or 'latest': {args.snapshot!r}"
            ) from exc
        member = fed.pin_member(ws, repo_id, snap_id)
    return {
        "workspace_id": ws.id, "workspace": ws.name,
        "repository_id": member.repository_id,
        "pinned_snapshot_id": member.pinned_snapshot_id,
    }


def _fed_cmd_view(fed: Federation, args: argparse.Namespace) -> dict[str, Any]:
    from code_atlas.federation import InvalidPinError

    ws = fed.get_workspace(args.name)
    pins: dict[int, int] = {}
    for seg in (args.pin or "").split(","):
        seg = seg.strip()
        if not seg:
            continue
        repo, sep, snap = seg.partition(":")
        if not sep or not repo.isdigit() or not snap.isdigit():
            raise InvalidPinError(
                "pin must be repo:snapshot[,repo:snapshot…]"
            )
        pins[int(repo)] = int(snap)
    return fed.resolve_view(ws, pins).to_dict()


_FED_COMMANDS = {
    "create": _fed_cmd_create,
    "add": _fed_cmd_add,
    "pin": _fed_cmd_pin,
    "view": _fed_cmd_view,
}

_FED_REQUIRED = {
    "create": ("name",), "add": ("name", "repo"),
    "pin": ("name", "repo", "snapshot"), "view": ("name",),
}


def run_federation(args: argparse.Namespace) -> int:
    """``federation`` (lf-0118, Phase 10): agrupamentos multi-repo.

    A mutação (create/add/pin) é superfície exclusiva da CLI — a API HTTP
    expõe somente views/lookups/graphs GET. Membros só entram de repos
    **já indexados**; nada de auto-discovery de diretórios.
    """
    from code_atlas.federation import Federation, FederationError

    cmd = getattr(args, "fed_cmd", "view") or "view"
    missing = [k for k in _FED_REQUIRED.get(cmd, ())
               if not getattr(args, k, None)]
    if missing:
        print(f"error: federation {cmd} requires "
              f"{', '.join(missing)}", file=sys.stderr)
        return EXIT_USAGE
    conn = _open_db()
    try:
        try:
            payload = _FED_COMMANDS[cmd](Federation(conn), args)
        except FederationError as exc:
            return _emit_error(args, str(exc))
    finally:
        conn.close()
    payload["tool"] = "code-atlas"
    payload["version"] = __version__
    payload["command"] = f"federation {cmd}"
    if args.json:
        print(json.dumps(payload))
    elif cmd == "view":
        print(f"view {payload['view_id']}")
        for m in payload["members"]:
            print(
                f"  repo={m['repository_id']} "
                f"snapshot={m['snapshot_id']} "
                f"[{m['resolution']}] {m['snapshot_status']}"
            )
    else:
        for k, v in payload.items():
            if not k.startswith("_") and k not in ("tool", "version",
                                                   "command"):
                print(f"{k}: {v}")
    return EXIT_OK


# --- domains (lf-0123, Cycle 7 Phase 15) ------------------------------------


def _domain_repo(
    conn: sqlite3.Connection, args: argparse.Namespace, ident: str | None
) -> tuple[int | None, str]:
    """Escopo do repo: ``--repo`` > ``--root`` > derivado do ``ident``.

    ``--repo`` aceita id | identity_key | root_handle | path (mesma
    resolução de ``federation add`` — reusa ``Federation.resolve_repository``).
    ``ident`` numérico/``dom:``/nome único deriva o repo do próprio dado
    — ``domains entity`` funciona sem flags quando a chave é exata.
    """
    repo_ref = getattr(args, "repo", None)
    root_arg = getattr(args, "root", None)
    if repo_ref:
        from code_atlas.federation import Federation, FederationError

        try:
            return Federation(conn).resolve_repository(str(repo_ref)), ""
        except FederationError as exc:
            return None, str(exc)
    if root_arg:
        root = resolve_root(root_arg)
        repo_id = StructuralSearch(conn).repo_for_root(str(root))
        if repo_id is None:
            return None, f"no indexed repository at {root}"
        return repo_id, ""
    if ident:
        from code_atlas.domains.cli_surface import repo_for_ident

        repo_id, derr = repo_for_ident(conn, ident)
        if derr:
            return None, derr
        if repo_id is not None:
            return repo_id, ""
    root = resolve_root(root_arg)
    repo_id = StructuralSearch(conn).repo_for_root(str(root))
    if repo_id is None:
        return None, f"no indexed repository at {root}"
    return repo_id, ""


def _domain_context(
    args: argparse.Namespace, ident: str | None = None
) -> tuple[
    sqlite3.Connection | None,
    int | None,
    int | None,
    dict[str, Any] | None,
    str,
]:
    """(conn, repo_id, snapshot_id, repo_info, err) — mesmo contrato de
    ``_search_context``, mas o repo pode vir de ``--repo``/``ident``."""
    conn = _open_db()
    repo_id, err = _domain_repo(conn, args, ident)
    if err or repo_id is None:
        conn.close()
        return None, None, None, None, err or "repository unresolved"
    search = StructuralSearch(conn)
    snap, serr = search.resolve_snapshot(
        repo_id, getattr(args, "snapshot", None))
    if serr or snap is None or snap.id is None:
        conn.close()
        return None, None, None, None, serr or "snapshot missing id"
    repo_info = search.repository_info(repo_id) or {"id": repo_id}
    return conn, repo_id, int(snap.id), repo_info, ""


def _dom_cmd_list(
    args: argparse.Namespace,
) -> tuple[dict[str, Any] | None, str]:
    """``domains list`` — adapters registrados (não extrai nada)."""
    from code_atlas.domains.cli_surface import adapters_report

    adapters = adapters_report()
    width = max((len(a["domain_id"]) for a in adapters), default=0)
    return {
        "command": "domains list",
        "adapters": adapters,
        "notes": [
            "registered adapters are opt-in: extraction only runs when "
            "`code-atlas index --domains` selects them",
        ],
        "_human": [
            "domain".ljust(width) + "  version  enabled  artifacts",
            *(
                f"{a['domain_id']:<{width}}  {a['adapter_version']:<8} "
                f"{'yes' if a['enabled'] else 'no':<7}  "
                + ",".join(a["artifact_support"]["extensions"] or ["-"])
                + (f"; globs={a['artifact_support']['globs']}"
                   if a["artifact_support"]["globs"] else "")
                for a in adapters
            ),
            "note: opt-in — `index --domains <ids|all>` enables extraction",
        ],
    }, ""


def _dom_cmd_entities(
    args: argparse.Namespace,
) -> tuple[dict[str, Any] | None, str]:
    """``domains entities`` — listagem determinística do snapshot."""
    from code_atlas.domains.cli_surface import (
        NOTE_OPT_IN,
        list_entities,
    )
    from code_atlas.domains.model import DomainEntityKind

    kind = (getattr(args, "kind", None) or "").strip()
    if kind and kind not in {str(k) for k in DomainEntityKind}:
        return None, (
            f"unknown --kind {kind!r} (valid: "
            f"{', '.join(sorted(str(k) for k in DomainEntityKind))})"
        )
    conn, repo_id, snap_id, repo_info, err = _domain_context(args)
    if err:
        return None, err
    assert conn is not None and repo_id is not None and snap_id is not None
    try:
        limit = clamp_limit(args.limit)
        offset = max(0, getattr(args, "offset", 0) or 0)
        domain = (getattr(args, "domain", None) or "").strip()
        q = (getattr(args, "q", None) or "").strip()
        out = list_entities(
            conn, repo_id, snap_id,
            domain=domain, kind=kind, q=q, limit=limit, offset=offset)
    finally:
        conn.close()
    notes = [NOTE_OPT_IN] if out["total"] == 0 else []
    items = out["items"]
    return {
        "command": "domains entities",
        "repository": repo_info,
        "snapshot_id": snap_id,
        "filters": {"domain": domain, "kind": kind, "q": q},
        "limit": limit,
        "offset": offset,
        "total": out["total"],
        "truncated": out["truncated"],
        "items": items,
        "notes": notes,
        "_human": [
            *(
                f"{i['entity_id']:>4} {i['domain']:<10} {i['kind']:<20} "
                f"{i['display_name']}  x{i['occurrences']}"
                f"{' ' + i['path'] if i['path'] else ''}"
                for i in items
            ),
            *notes,
        ],
    }, ""


def _dom_cmd_entity(
    args: argparse.Namespace,
) -> tuple[dict[str, Any] | None, str]:
    """``domains entity <key>`` — cartão de identidade + proveniência."""
    from code_atlas.domains.cli_surface import (
        entity_detail,
        resolve_single_entity,
    )

    ident = str(args.ident)
    conn, repo_id, snap_id, repo_info, err = _domain_context(args, ident)
    if err:
        return None, err
    assert conn is not None and repo_id is not None and snap_id is not None
    try:
        entity, eerr = resolve_single_entity(conn, repo_id, ident)
        if entity is None:
            return None, eerr
        detail = entity_detail(conn, entity, snap_id)
    finally:
        conn.close()
    identd = detail["identity"]
    notes: list[str] = []
    if not detail["occurrences"]:
        notes.append(
            f"no occurrences in snapshot {snap_id} — entity was "
            "extracted under another snapshot or a different "
            "enablement set"
        )
    human = [
        f"entity #{detail['entity_id']} {identd['display_name']} "
        f"({identd['kind']}) — domain={identd['domain']}",
        f"identity_key: {identd['identity_key']}",
        f"  catalog={identd['catalog'] or '-'} "
        f"namespace={identd['namespace'] or '-'} "
        f"environment={identd['environment'] or '-'} "
        f"name={identd['name']}",
        f"occurrences (snapshot {snap_id}): "
        f"{len(detail['occurrences'])}",
        *(
            f"  {o['path'] or '<synthesized>'}:{o['line'] or '-'}:"
            f"{o['col'] or '-'} [{o['extraction_status']}]"
            for o in detail["occurrences"]
        ),
        f"relations: {sum(r['count'] for r in detail['relations_summary'])}",
        *(
            f"  {r['direction']:<4} {r['type']:<20} {r['status']:<11} "
            f"x{r['count']}"
            for r in detail["relations_summary"]
        ),
        f"evidence: {detail['evidence_count']} distinct row(s)",
        *notes,
    ]
    return {
        "command": "domains entity",
        "repository": repo_info,
        "snapshot_id": snap_id,
        "entity": detail,
        "notes": notes,
        "_human": human,
    }, ""


def _dom_cmd_lineage(
    args: argparse.Namespace,
) -> tuple[dict[str, Any] | None, str]:
    """``domains lineage <table>`` — traversal de fluxo bounded."""
    from code_atlas.domains.cli_surface import (
        DATA_FLOW_TYPES,
        lineage,
        resolve_single_entity,
    )

    ident = str(args.ident)
    conn, repo_id, snap_id, repo_info, err = _domain_context(args, ident)
    if err:
        return None, err
    assert conn is not None and repo_id is not None and snap_id is not None
    try:
        entity, eerr = resolve_single_entity(conn, repo_id, ident)
        if entity is None:
            return None, eerr
        out = lineage(
            conn, entity, snap_id,
            direction=getattr(args, "direction", "both"),
            depth=clamp_depth(getattr(args, "depth", 2)),
            limit=clamp_limit(getattr(args, "limit", None)))
    finally:
        conn.close()
    types = ", ".join(sorted(DATA_FLOW_TYPES))
    notes = (
        [f"no data-flow relations ({types}) touch this entity in "
         f"snapshot {snap_id}"]
        if not out["edges"] else []
    )
    ent = out["entity"]
    human = [
        f"lineage of {ent['display_name']} ({ent['kind']}) — "
        f"direction={out['direction']} depth<={out['depth']} "
        f"limit={out['limit']} truncated={out['truncated']}",
    ]
    if out["direction"] in ("up", "both"):
        human.append(f"upstream (feeds {ent['display_name']}):")
        human += [
            f"  d{e['depth']} {e['from']} → {e['to']} "
            f"[{e['type']} {e['resolution_status']}]"
            for e in out["edges"] if e["via"] == "up"
        ]
    if out["direction"] in ("down", "both"):
        human.append(f"downstream (fed by {ent['display_name']}):")
        human += [
            f"  d{e['depth']} {e['from']} → {e['to']} "
            f"[{e['type']} {e['resolution_status']}]"
            for e in out["edges"] if e["via"] == "down"
        ]
    human += notes
    payload = {
        "command": "domains lineage",
        "repository": repo_info,
        "snapshot_id": snap_id,
        "notes": notes,
        "_human": human,
    }
    payload.update(out)
    return payload, ""


_DOM_COMMANDS = {
    "list": _dom_cmd_list,
    "entities": _dom_cmd_entities,
    "entity": _dom_cmd_entity,
    "lineage": _dom_cmd_lineage,
}

_DOM_REQUIRED = {
    "entity": ("ident",),
    "lineage": ("ident",),
}


def run_domains(args: argparse.Namespace) -> int:
    """``domains`` (lf-0123, Phase 15): superfície read-only de domínio.

    Todos os subcomandos são somente leitura e ancorados no snapshot
    resolvido — adapters desabilitados produzem resposta vazia com
    nota, nunca erro.
    """
    cmd = getattr(args, "dom_cmd", "list") or "list"
    missing = [k for k in _DOM_REQUIRED.get(cmd, ())
               if not getattr(args, k, None)]
    if missing:
        print(f"error: domains {cmd} requires "
              f"{', '.join(missing)}", file=sys.stderr)
        return EXIT_USAGE
    try:
        payload, err = _DOM_COMMANDS[cmd](args)
    except KeyError:
        print(f"error: unknown domains command {cmd!r}", file=sys.stderr)
        return EXIT_USAGE
    if err or payload is None:
        return _emit_error(args, err or "domains command failed")
    payload["tool"] = "code-atlas"
    payload["version"] = __version__
    payload["schema_version"] = SCHEMA_VERSION
    return _emit_page(args, payload)


def run_dependencies(args: argparse.Namespace) -> int:
    """``dependencies`` (lf-0022): arestas saíntes, BFS limitado."""
    conn, repo_id, snap_id, repo_info, err = _search_context(args)
    if err:
        return _emit_error(args, err)
    assert (
        conn is not None and repo_id is not None and snap_id is not None
    )
    depth = clamp_depth(args.depth)
    try:
        search = StructuralSearch(conn)
        out, derr = search.dependencies(
            repo_id, snap_id, args.ident, depth,
            clamp_limit(args.limit), max(0, args.offset),
        )
        if out is None:
            return _emit_error(args, derr)
        results = out["results"]
        payload: dict[str, Any] = {
            "tool": "code-atlas", "version": __version__,
            "schema_version": SCHEMA_VERSION, "command": "dependencies",
            "query": args.ident, "snapshot_id": snap_id,
            "repository": repo_info, "diagnostics": [],
            "symbol_id": out["symbol_id"],
            "qualified_name": out["qualified_name"],
            "depth": depth, "limit": clamp_limit(args.limit),
            "offset": max(0, args.offset),
            "total": out["total"], "truncated": out["truncated"],
            "visited_symbols": out["visited_symbols"],
            "results": results,
            "_human": [
                f"deps of {out['qualified_name']} "
                f"(depth={depth}, visited={out['visited_symbols']})",
                *(
                    f"  d{r['depth']} {r['type']} -> "
                    f"{r['to']['symbol'] if r['to'] else r['to_unresolved']}"
                    f" [{r['resolution_status']}]"
                    for r in results
                ),
            ],
        }
    finally:
        conn.close()
    return _emit_page(args, payload)


def clamp_depth(depth: int | None) -> int:
    """Sane default + teto duro de traversal (lf-0022)."""
    if depth is None:
        return 1
    return max(1, min(int(depth), MAX_DEPTH))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="code-atlas",
        description="Code Atlas — inteligência estrutural de código local-first (Cycle 1).",
        epilog="exit codes: 0 ok | 1 error | 2 usage | 3 unsafe/blocked",
    )
    parser.add_argument("--version", action="version", version=f"code-atlas {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_doctor = sub.add_parser("doctor", help="verifica ambiente, config e dependências")
    p_doctor.add_argument("--json", action="store_true", help="saída em JSON")
    p_doctor.set_defaults(func=run_doctor)

    p_init = sub.add_parser("init", help="inicializa config, data dir e registro do repo")
    p_init.add_argument("--root", metavar="PATH", help="raiz do workspace (default: cwd)")
    p_init.add_argument("--force", action="store_true", help="sobrescreve config existente")
    p_init.add_argument("--json", action="store_true", help="saída em JSON")
    p_init.set_defaults(func=run_init)

    p_index = sub.add_parser("index", help="indexa inventário de um diretório (snapshot)")
    p_index.add_argument("path", nargs="?", metavar="PATH", help="diretório a indexar")
    p_index.add_argument("--root", metavar="PATH", help="raiz alternativa (se PATH ausente)")
    p_index.add_argument(
        "--full", action="store_true", help="força re-scan mesmo sem mudanças"
    )
    p_index.add_argument(
        "--incremental", action="store_true", default=True,
        help="indexação incremental (default ativo; --full desativa)",
    )
    p_index.add_argument(
        "--scip", metavar="PATH", default=None,
        help="índice SCIP ad-hoc (sobrescreve [resolvers.scip].index_path)",
    )
    p_index.add_argument(
        "--resolver", metavar="NAME", action="append", default=None,
        help="restringe resolvers ativos (syntactic|scip|lsp|lsp:<nome>); "
             "repetível ou separado por vírgula",
    )
    p_index.add_argument(
        "--domains", metavar="LIST", default=None,
        help="adapters de domínio opt-in: ids separados por vírgula "
             "(sql,pyspark,aws-data,terraform,cicd,api) ou 'all' — "
             "default OFF (ver `code-atlas domains list`)",
    )
    p_index.add_argument(
        "--cross-domain", action="store_true",
        help="resolução cross-domain no snapshot (requer --domains)",
    )
    p_index.add_argument(
        "--verbose", action="store_true",
        help="progresso por estágio em stderr: ms por etapa + files/s "
             "no parse (stdout e --json não são tocados)",
    )
    p_index.add_argument("--json", action="store_true", help="saída em JSON")
    p_index.set_defaults(func=run_index)

    p_sem = sub.add_parser(
        "semantic-status",
        help="resolvers instalados/habilitados/usados + cobertura semântica",
    )
    p_sem.add_argument("path", nargs="?", metavar="PATH",
                       help="diretório do workspace")
    p_sem.add_argument("--root", metavar="PATH",
                       help="raiz alternativa (se PATH ausente)")
    p_sem.add_argument("--json", action="store_true", help="saída em JSON")
    p_sem.set_defaults(func=run_semantic_status)

    p_plan = sub.add_parser(
        "index-plan", help="dry-run: plano de indexação em JSON (zero writes)"
    )
    p_plan.add_argument("path", nargs="?", metavar="PATH",
                        help="diretório a planejar")
    p_plan.add_argument("--root", metavar="PATH",
                        help="raiz alternativa (se PATH ausente)")
    p_plan.add_argument("--json", action="store_true", help="saída em JSON")
    p_plan.set_defaults(func=run_index_plan)

    p_status = sub.add_parser("status", help="estado do índice (git, snapshot, migrações)")
    p_status.add_argument("--root", metavar="PATH", help="raiz do workspace (default: cwd)")
    p_status.add_argument("--json", action="store_true", help="saída em JSON")
    p_status.set_defaults(func=run_status)

    p_search = sub.add_parser(
        "search", help="busca estrutural por símbolo (substring)"
    )
    p_search.add_argument("query", metavar="QUERY", help="substring do qualified_name")
    p_search.add_argument(
        "--strategy", metavar="NAME", default="structural",
        help="structural|lexical|graph|hybrid (default structural)")
    _add_query_args(p_search, p_path=True)
    p_search.set_defaults(func=run_search)

    p_ctx = sub.add_parser(
        "context", help="context pack sob budget de tokens (lf-0040)")
    p_ctx.add_argument("task", metavar="TASK", help="descrição da tarefa")
    p_ctx.add_argument("--level", choices=("orientation", "focus", "deep"),
                       default="focus", help="nível de detalhe")
    p_ctx.add_argument("--budget", metavar="TOKENS", type=int, default=4000,
                       help="orçamento estimado (default 4000)")
    p_ctx.add_argument("--format", choices=("json", "markdown", "human"),
                       default="human", help="renderer de saída")
    _add_query_args(p_ctx, filters=False)
    p_ctx.set_defaults(func=run_context)

    p_exp = sub.add_parser(
        "context-expand",
        help="expande símbolo no MESMO snapshot do pack (lf-0041)")
    p_exp.add_argument("--symbol", metavar="SYMBOL", required=True,
                       help="qualified_name (ou sufixo) a expandir")
    p_exp.add_argument("--delivered", metavar="IDS", default="",
                       help="item_ids já entregues (csv) — dedup")
    p_exp.add_argument("--budget", metavar="TOKENS", type=int,
                       default=4000)
    _add_query_args(p_exp, filters=False)
    p_exp.set_defaults(func=run_context_expand)

    p_mcp = sub.add_parser(
        "mcp", help="servidor MCP stdio — JSON-RPC ndjson (lf-0042)")
    p_mcp.add_argument("cmd", nargs="?", default="serve",
                       choices=("serve", "doctor", "manifest"))
    p_mcp.add_argument("--host", default="generic",
                       choices=("claude-code", "codex-cli", "generic"))
    p_mcp.add_argument("--json", action="store_true")
    p_mcp.set_defaults(func=run_mcp)

    p_symbol = sub.add_parser("symbol", help="detalhe de um símbolo + ocorrências")
    p_symbol.add_argument("ident", metavar="SYMBOL", help="id numérico ou qualified_name")
    _add_query_args(p_symbol, filters=False)
    p_symbol.set_defaults(func=run_symbol)

    p_refs = sub.add_parser("references", help="relações que tocam um símbolo")
    p_refs.add_argument("ident", metavar="SYMBOL", help="id numérico ou qualified_name")
    p_refs.add_argument("--relation-type", metavar="TYPE", help="filtra por EdgeKind")
    _add_query_args(p_refs, filters=False)
    p_refs.set_defaults(func=run_references)

    p_graph = sub.add_parser("graph", help="dump de nós+arestas do snapshot (JSON)")
    p_graph.add_argument("--relation-type", metavar="TYPE", help="filtra arestas")
    _add_query_args(p_graph, p_path=True)
    p_graph.set_defaults(func=run_graph)

    p_defs = sub.add_parser(
        "definitions", help="sites de definição de um símbolo (path:span)"
    )
    p_defs.add_argument("ident", metavar="SYMBOL",
                        help="id numérico ou qualified_name")
    _add_query_args(p_defs, filters=False)
    p_defs.set_defaults(func=run_definitions)

    p_deps = sub.add_parser(
        "dependencies", help="arestas saíntes de um símbolo (BFS limitado)"
    )
    p_deps.add_argument("ident", metavar="SYMBOL",
                        help="id numérico ou qualified_name")
    p_deps.add_argument(
        "--depth", metavar="N", type=int, default=1,
        help=f"profundidade de traversal (default 1, teto {MAX_DEPTH})",
    )
    _add_query_args(p_deps, filters=False)
    p_deps.set_defaults(func=run_dependencies)

    p_impact = sub.add_parser(
        "impact", help="análise de impacto entre revisões (Cycle 5)")
    p_impact.add_argument(
        "cmd", nargs="?", default="report",
        choices=("diff", "worktree", "symbol", "tests", "contract",
                 "report"))
    p_impact.add_argument("ident", nargs="?", metavar="SYMBOL",
                          help="qualified_name para symbol/contract")
    p_impact.add_argument("--base", metavar="REV", default="",
                          help="revisão base (HEAD~1, sha, branch)")
    p_impact.add_argument("--target", metavar="REV", default="",
                          help="revisão alvo (vazio = worktree)")
    p_impact.add_argument("--depth", metavar="N", type=int, default=4)
    p_impact.add_argument("--max-nodes", metavar="N", type=int,
                          default=500)
    p_impact.add_argument("--max-paths", metavar="N", type=int,
                          default=50)
    p_impact.add_argument("--known-only", action="store_true")
    p_impact.add_argument("--format",
                          choices=("json", "markdown", "human"),
                          default="human")
    _add_query_args(p_impact, filters=False)
    p_impact.set_defaults(func=run_impact)

    p_api = sub.add_parser(
        "api", help="API HTTP local para o Explorer (loopback, read-only)")
    p_api.add_argument("--host", metavar="HOST", default="127.0.0.1",
                       help="bind (default 127.0.0.1 — público proibido)")
    p_api.add_argument("--port", metavar="N", type=int, default=8741)
    p_api.add_argument("--verbose", action="store_true")
    p_api.set_defaults(func=run_api)

    p_expl = sub.add_parser(
        "explorer",
        help="inicia o Explorer visual (API + assets compilados)")
    p_expl.add_argument("--host", metavar="HOST", default="127.0.0.1")
    p_expl.add_argument("--port", metavar="N", type=int, default=8741)
    p_expl.add_argument("--open", action="store_true",
                       help="abre o navegador (somente quando pedido)")
    p_expl.add_argument("--verbose", action="store_true")
    p_expl.set_defaults(func=run_explorer)

    p_fed = sub.add_parser(
        "federation",
        help="agrupa repos indexados numa view federada "
             "(lf-0118; mutação via CLI)")
    p_fed.add_argument(
        "fed_cmd", metavar="CMD",
        choices=("create", "add", "pin", "view"),
        help="create <name> | add <ws> <repo> | pin <ws> <repo> <snap|latest>"
             " | view <ws>")
    p_fed.add_argument("name", nargs="?", metavar="NAME",
                       help="nome a criar (create) ou ref <ws> (demais)")
    p_fed.add_argument("repo", nargs="?", metavar="REPO",
                       help="repo id | identity_key | root_handle | path "
                            "(add/pin)")
    p_fed.add_argument("snapshot", nargs="?", metavar="SNAPSHOT",
                       help="snapshot id a pinar, ou 'latest' para desfazer "
                            "o pin (pin)")
    p_fed.add_argument("--pin", metavar="REPO:SNAP,…", default="",
                       help="overrides de pin na view (view)")
    p_fed.add_argument("--json", action="store_true", help="saída em JSON")
    p_fed.set_defaults(func=run_federation)

    p_dom = sub.add_parser(
        "domains",
        help="inteligência de domínio read-only (lf-0123): adapters, "
             "entidades e linhagem — dados existem só quando o índice "
             "rodou com `index --domains`")
    p_dom.add_argument(
        "dom_cmd", metavar="CMD",
        choices=("list", "entities", "entity", "lineage"),
        help="list | entities | entity <key> | lineage <table>")
    p_dom.add_argument(
        "ident", nargs="?", metavar="KEY",
        help="identity_key 'dom:…' | id numérico | qualified_name | "
             "namespace.qualified_name (entity/lineage)")
    p_dom.add_argument(
        "--repo", metavar="REF",
        help="repo id | identity_key | root_handle | path "
             "(como `federation add`)")
    p_dom.add_argument("--domain", metavar="D",
                       help="filtra por domain_id (entities)")
    p_dom.add_argument("--kind", metavar="K",
                       help="filtra por DomainEntityKind (entities)")
    p_dom.add_argument("--q", metavar="SUBSTR",
                       help="substring de qualified_name/name (entities)")
    p_dom.add_argument(
        "--direction", choices=("up", "down", "both"), default="both",
        help="up=produtores, down=consumidores (lineage; default both)")
    p_dom.add_argument(
        "--depth", metavar="N", type=int, default=2,
        help=f"hops do traversal (lineage; default 2, teto {MAX_DEPTH})")
    p_dom.add_argument(
        "--limit", metavar="N", type=int,
        help=f"máx. de resultados (default {DEFAULT_LIMIT}, teto {MAX_LIMIT})")
    p_dom.add_argument("--offset", metavar="N", type=int, default=0,
                       help="deslocamento de paginação (entities)")
    p_dom.add_argument("--snapshot", metavar="ID", type=int,
                       help="snapshot alvo (default: READY mais recente)")
    p_dom.add_argument("--root", metavar="PATH",
                       help="raiz do workspace (default: cwd)")
    p_dom.add_argument("--json", action="store_true", help="saída em JSON")
    p_dom.set_defaults(func=run_domains)

    return parser


def _add_query_args(
    p: argparse.ArgumentParser, *, filters: bool = True, p_path: bool = False
) -> None:
    """Args comuns dos comandos read-only de consulta."""
    p.add_argument("--root", metavar="PATH", help="raiz do workspace (default: cwd)")
    p.add_argument("--snapshot", metavar="ID", type=int,
                   help="snapshot alvo (default: READY mais recente)")
    p.add_argument("--limit", metavar="N", type=int,
                   help=f"máx. de resultados (default {DEFAULT_LIMIT}, teto {MAX_LIMIT})")
    p.add_argument("--offset", metavar="N", type=int, default=0,
                   help="deslocamento de paginação")
    if filters:
        p.add_argument("--language", metavar="LANG", help="filtra por linguagem")
        p.add_argument("--kind", metavar="KIND", help="filtra por NodeKind")
        if p_path:
            p.add_argument("--path", dest="path_filter", metavar="SUBSTR",
                           help="substring de relative_path")
    else:
        p.set_defaults(language=None, kind=None, path_filter=None)
    p.add_argument("--json", action="store_true", help="saída em JSON")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))
