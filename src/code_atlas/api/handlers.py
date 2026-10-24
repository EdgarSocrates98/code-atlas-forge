"""Handlers da API v1 — reutilizam os engines de domínio, nunca SQL solto.

Cada handler abre uma conexão read-only por request (ciclo de vida real,
mesmo padrão do MCP) e retorna ``dict`` serializável. Erros viram
``ApiError`` tipados — nunca traceback ao cliente.
"""

from __future__ import annotations

import dataclasses
import json
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

from code_atlas.api.server import ApiError
from code_atlas.storage.store import Store

if TYPE_CHECKING:
    from http.server import BaseHTTPRequestHandler

    from code_atlas.api.server import _AtlasHttpd

_MAX_LIMIT = 200
_QNAME = re.compile(r"^[a-zA-Z0-9_:.<>$/\-]{1,300}$")


def _qs(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    raw = parse_qs(urlparse(handler.path).query)
    return {k: v[0] for k, v in raw.items() if v}


def _int(params: dict[str, str], key: str, default: int = 0,
         cap: int = _MAX_LIMIT) -> int:
    try:
        v = int(params.get(key, "") or default)
    except ValueError as exc:
        raise ApiError("bad-param", f"parâmetro {key} não é inteiro") from exc
    return max(0, min(v, cap))


def _repo(conn: Any, params: dict[str, str]) -> int:
    """Resolve repository_id — escopo obrigatório (Cycle 5.1)."""
    store = Store(conn)
    rid = params.get("repository_id", "")
    if rid:
        try:
            rid_int = int(rid)
        except ValueError as exc:
            raise ApiError("bad-param", "repository_id inválido") from exc
        if store.repository_by_id(rid_int) is None:
            raise ApiError("repo-not-found", "repositório inexistente", 404)
        return rid_int
    repos = [r for r in _all_repos(store)]
    if not repos:
        raise ApiError("no-repository", "nenhum repositório indexado", 404)
    return repos[0]


def _all_repos(store: Store) -> list[int]:
    rows = store.conn.execute(
        "SELECT id FROM repositories ORDER BY id").fetchall()
    return [int(r["id"]) for r in rows]


def _snapshot(conn: Any, repo_id: int, params: dict[str, str]) -> int:
    store = Store(conn)
    sid = params.get("snapshot_id", "")
    if sid:
        try:
            snap = store.snapshot_by_id(int(sid))
        except ValueError as exc:
            raise ApiError("bad-param", "snapshot_id inválido") from exc
        if snap is None or snap.repository_id != repo_id:
            raise ApiError("snapshot-not-found",
                           "snapshot inexistente no repositório", 404)
        if snap.status != "READY":
            raise ApiError("snapshot-not-ready",
                           f"snapshot {snap.id} está {snap.status}", 409)
        assert snap.id is not None
        return snap.id
    snap = store.latest_ready_snapshot(repo_id)
    if snap is None:
        raise ApiError("snapshot-not-ready", "nenhum snapshot READY", 409)
    assert snap.id is not None
    return snap.id


_NODE_SELECT = (
    "SELECT o.id AS occurrence_id, o.logical_symbol_id AS symbol_id, "
    "s.qualified_name AS qname, s.kind AS kind, s.language AS language, "
    "f.relative_path AS path, o.span_start, o.span_end, o.parse_status "
    "FROM symbol_occurrences o "
    "JOIN symbols s ON s.id = o.logical_symbol_id "
    "JOIN source_files f ON f.id = o.source_file_id ")


def _row_to_node(row: Any) -> dict[str, Any]:
    return {
        "id": f"occ:{row['occurrence_id']}",
        "occurrence_id": row["occurrence_id"],
        "symbol_id": row["symbol_id"],
        "qname": row["qname"],
        "kind": row["kind"],
        "language": row["language"],
        "path": row["path"],
        "span": {"start": row["span_start"], "end": row["span_end"]},
        "parse_status": row["parse_status"],
    }


_NODES_IN_QUERY = (
    _NODE_SELECT +
    "WHERE o.snapshot_id = ? AND o.id IN (SELECT value FROM json_each(?))")
_NODE_BY_ID_QUERY = _NODE_SELECT + "WHERE o.snapshot_id = ? AND o.id = ?"


_COUNT_QUERIES = {
    "file_inventory":
        "SELECT COUNT(*) AS n FROM file_inventory WHERE snapshot_id = ?",
    "symbol_occurrences":
        "SELECT COUNT(*) AS n FROM symbol_occurrences WHERE snapshot_id = ?",
    "relations":
        "SELECT COUNT(*) AS n FROM relations WHERE snapshot_id = ?",
    "evidence":
        "SELECT COUNT(*) AS n FROM evidence WHERE snapshot_id = ?",
}


# --- handlers -----------------------------------------------------------


def _health(server: _AtlasHttpd, conn: Any, _p: dict[str, str],
            _b: dict[str, Any] | None) -> dict[str, Any]:
    return {"status": "ok", "service": "code-atlas-api", "version": 1}


def _capabilities(server: _AtlasHttpd, conn: Any, _p: dict[str, str],
                  _b: dict[str, Any] | None) -> dict[str, Any]:
    from code_atlas.search.engine import SearchEngine
    caps = dataclasses.asdict(SearchEngine(conn).capabilities())
    return {"search": caps, "api_version": 1, "readonly": True}


def _repositories(server: _AtlasHttpd, conn: Any, _p: dict[str, str],
                  _b: dict[str, Any] | None) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT id, identity_key, root_handle FROM repositories ORDER BY id"
    ).fetchall()
    items = [{"id": r["id"], "identity_key": r["identity_key"],
              "root_handle": r["root_handle"]} for r in rows]
    return {"items": items, "total": len(items)}


def _snapshots(server: _AtlasHttpd, conn: Any, repo_id: str,
               _b: dict[str, Any] | None) -> dict[str, Any]:
    try:
        rid = int(repo_id)
    except ValueError as exc:
        raise ApiError("bad-param", "repository_id inválido") from exc
    rows = conn.execute(
        "SELECT id, status, commit_sha, git_ref, created_at FROM snapshots "
        "WHERE repository_id = ? ORDER BY id DESC LIMIT 100", (rid,)).fetchall()
    return {"items": [dict(r) for r in rows]}


def _graph_overview(server: _AtlasHttpd, conn: Any, p: dict[str, str],
                    _b: dict[str, Any] | None) -> dict[str, Any]:
    """Visão macro: nós agregados por arquivo — nunca o grafo inteiro."""
    rid = _repo(conn, p)
    sid = _snapshot(conn, rid, p)
    limit = _int(p, "limit", 500, cap=2000)
    rows = conn.execute(
        "SELECT f.relative_path AS path, COUNT(*) AS symbols, "
        "MIN(s.language) AS language "
        "FROM symbol_occurrences o "
        "JOIN symbols s ON s.id = o.logical_symbol_id "
        "JOIN source_files f ON f.id = o.source_file_id "
        "WHERE o.snapshot_id = ? "
        "GROUP BY f.relative_path ORDER BY symbols DESC LIMIT ?",
        (sid, limit)).fetchall()
    total = conn.execute(
        "SELECT COUNT(DISTINCT source_file_id) AS n FROM symbol_occurrences "
        "WHERE snapshot_id = ?", (sid,)).fetchone()["n"]
    return {"snapshot_id": sid, "repository_id": rid,
            "items": [dict(r) for r in rows], "total_files": total,
            "truncated": len(rows) < total}


def _graph_neighbors(server: _AtlasHttpd, conn: Any, p: dict[str, str],
                     _b: dict[str, Any] | None) -> dict[str, Any]:
    rid = _repo(conn, p)
    sid = _snapshot(conn, rid, p)
    direction = p.get("direction", "out")
    if direction not in ("in", "out", "both"):
        raise ApiError("bad-param", "direction deve ser in|out|both")
    limit = _int(p, "limit", 50)
    occ_id = _int(p, "occurrence_id", 0, cap=10**9)
    if not occ_id:
        raise ApiError("bad-param", "occurrence_id obrigatório")
    edges = conn.execute(
        "SELECT id, from_id, to_id, type, resolution_status, resolver "
        "FROM relations WHERE snapshot_id = ? AND ("
        "  (? = 'out' AND from_id = ?) "
        "  OR (? = 'in' AND to_id = ?) "
        "  OR (? = 'both' AND (from_id = ? OR to_id = ?))"
        ") LIMIT ?",
        (sid, direction, occ_id, direction, occ_id,
         direction, occ_id, occ_id, limit)).fetchall()
    occ_ids = {e["from_id"] for e in edges} | {e["to_id"] for e in edges}
    occ_ids.discard(0)
    occ_ids.discard(None)
    nodes = []
    if occ_ids:
        nodes = conn.execute(
            _NODES_IN_QUERY,
            (sid, json.dumps(sorted(occ_ids)))).fetchall()
    return {"snapshot_id": sid,
            "edges": [dict(e) for e in edges],
            "nodes": [_row_to_node(n) for n in nodes],
            "truncated": len(edges) == limit}


def _symbol(server: _AtlasHttpd, conn: Any, sym_id: str,
            p: dict[str, str]) -> dict[str, Any]:
    rid = _repo(conn, p)
    sid = _snapshot(conn, rid, p)
    try:
        occ_id = int(sym_id)
    except ValueError as exc:
        raise ApiError("bad-param", "id de símbolo deve ser occurrence_id") from exc
    row = conn.execute(_NODE_BY_ID_QUERY, (sid, occ_id)).fetchone()
    if row is None:
        raise ApiError("symbol-not-found", "símbolo inexistente", 404)
    counts = conn.execute(
        "SELECT type, resolution_status, COUNT(*) AS n FROM relations "
        "WHERE snapshot_id = ? AND (from_id = ? OR to_id = ?) GROUP BY 1,2",
        (sid, occ_id, occ_id)).fetchall()
    out = _row_to_node(row)
    out["relations_summary"] = [dict(c) for c in counts]
    return out


def _search(server: _AtlasHttpd, conn: Any, p: dict[str, str],
            _b: dict[str, Any] | None) -> dict[str, Any]:
    from code_atlas.search.contracts import SearchRequest
    from code_atlas.search.engine import SearchEngine
    q = p.get("q", "")
    if not q or len(q) > 300:
        raise ApiError("bad-param", "q obrigatório (≤300 chars)")
    root = p.get("repository_root", "")
    if not root:
        first = conn.execute(
            "SELECT root_handle FROM repositories ORDER BY id LIMIT 1"
        ).fetchone()
        root = str(first["root_handle"]) if first else ""
    elif Store(conn).repository_id_for(root) is None:
        raise ApiError("repo-not-indexed", "repositório não indexado", 404)
    res = SearchEngine(conn).search(SearchRequest(
        query=q,
        strategy=p.get("strategy", "hybrid") if p.get("strategy", "hybrid") in
        ("lexical", "structural", "hybrid", "vector", "graph") else "hybrid",
        repository_root=root,
        snapshot_id=_int(p, "snapshot_id", 0, cap=10**9) or None,
        limit=_int(p, "limit", 20, cap=100) or 20,
        cursor=p.get("cursor") or None,
        language=p.get("language") or None,
        kind=p.get("kind") or None,
        path=p.get("path") or None))
    return dataclasses.asdict(res)


def _context(server: _AtlasHttpd, conn: Any, _p: dict[str, str],
             body: dict[str, Any] | None) -> dict[str, Any]:
    from code_atlas.context.contracts import ContextRequest
    from code_atlas.context.engine import ContextEngine
    body = body or {}
    task = str(body.get("task", ""))[:2000]
    if not task:
        raise ApiError("bad-param", "task obrigatória")
    pack = ContextEngine(conn).build(ContextRequest(
        task=task,
        repository_root=str(body.get("repository_root", ""))[:1000],
        snapshot_id=body.get("snapshot_id"),
        level=body.get("level", "focus"),
        budget_tokens=min(int(body.get("budget_tokens", 4000)), 64000),
        max_symbols=min(int(body.get("max_symbols", 50)), 200)))
    return pack.to_dict()


def _impact(server: _AtlasHttpd, conn: Any, _p: dict[str, str],
            body: dict[str, Any] | None) -> dict[str, Any]:
    from code_atlas.impact.contracts import ImpactRequest
    from code_atlas.impact.engine import ImpactEngine
    body = body or {}
    root = str(body.get("repository_root", ""))
    if not root or len(root) > 1000:
        raise ApiError("bad-param", "repository_root obrigatório")
    res = ImpactEngine(conn).analyze(ImpactRequest(
        repository_root=root,
        base_revision=str(body.get("base_revision", "HEAD"))[:200],
        target_revision=str(body.get("target_revision", ""))[:200],
        symbol=str(body.get("symbol"))[:300] if body.get("symbol") else None,
        file=str(body.get("file"))[:1000] if body.get("file") else None,
        base_snapshot_id=body.get("base_snapshot_id"),
        target_snapshot_id=body.get("target_snapshot_id"),
        include_possible=bool(body.get("include_possible", True)),
        known_only=bool(body.get("known_only", False))))
    return dataclasses.asdict(res)


def _evidence(server: _AtlasHttpd, conn: Any, ev_id: str,
              p: dict[str, str]) -> dict[str, Any]:
    rid = _repo(conn, p)
    sid = _snapshot(conn, rid, p)
    try:
        occ_id = int(ev_id)
    except ValueError as exc:
        raise ApiError("bad-param", "id de evidência inválido") from exc
    row = conn.execute(
        "SELECT id, extractor, extractor_version, notes, path, span, "
        "content_hash FROM evidence WHERE snapshot_id = ? AND id = ?",
        (sid, occ_id)).fetchone()
    if row is None:
        raise ApiError("evidence-not-found", "evidência inexistente", 404)
    refs = conn.execute(
        "SELECT id, from_id, to_id, type, resolution_status FROM relations "
        "WHERE snapshot_id = ? AND evidence_ids LIKE ? LIMIT 100",
        (sid, f"%{occ_id}%")).fetchall()
    return {"snapshot_id": sid, "evidence": dict(row),
            "referencing_relations": [dict(r) for r in refs]}


def _index_status(server: _AtlasHttpd, conn: Any, _p: dict[str, str],
                  _b: dict[str, Any] | None) -> dict[str, Any]:
    repos = conn.execute(
        "SELECT r.id, r.root_handle, "
        "(SELECT COUNT(*) FROM snapshots s WHERE s.repository_id = r.id "
        " AND s.status = 'READY') AS ready, "
        "(SELECT MAX(id) FROM snapshots s WHERE s.repository_id = r.id "
        " AND s.status = 'READY') AS latest_ready "
        "FROM repositories r ORDER BY r.id").fetchall()
    items = []
    for r in repos:
        sid = r["latest_ready"]
        counts = {}
        if sid:
            for tbl, sql in _COUNT_QUERIES.items():
                counts[tbl] = conn.execute(sql, (sid,)).fetchone()["n"]
        items.append({"repository_id": r["id"], "root_handle": r["root_handle"],
                      "ready_snapshots": r["ready"], "latest_ready": sid,
                      "counts": counts})
    return {"items": items}


def handle(server: _AtlasHttpd, method: str, route: str,
           handler: BaseHTTPRequestHandler) -> Any:
    conn = server.connect()
    try:
        p = _qs(handler)
        body = server_body(handler) if method == "POST" else None
        if method == "GET":
            m = re.fullmatch(r"/repositories/(\d+)/snapshots", route)
            if m:
                return _snapshots(server, conn, m.group(1), body)
            m = re.fullmatch(r"/symbols/(\d+)", route)
            if m:
                return _symbol(server, conn, m.group(1), p)
            m = re.fullmatch(r"/evidence/(\d+)", route)
            if m:
                return _evidence(server, conn, m.group(1), p)
            simple = {
                "/health": _health, "/capabilities": _capabilities,
                "/repositories": _repositories,
                "/graph/overview": _graph_overview,
                "/graph/neighbors": _graph_neighbors,
                "/search": _search, "/index/status": _index_status,
            }
            fn = simple.get(route)
            if fn:
                return fn(server, conn, p, body)
        elif method == "POST":
            if route == "/context":
                return _context(server, conn, p, body)
            if route == "/impact/analyze":
                return _impact(server, conn, p, body)
        raise ApiError("not-found", f"{method} {route} inexistente", 404)
    finally:
        conn.close()


def server_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    if length > 64 * 1024:
        raise ApiError("payload-too-large", "corpo excede 64 KiB", 413)
    import json
    raw = handler.rfile.read(length) if length else b"{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ApiError("bad-json", "corpo não é JSON válido") from exc
    if not isinstance(data, dict):
        raise ApiError("bad-json", "corpo deve ser objeto JSON")
    return data
