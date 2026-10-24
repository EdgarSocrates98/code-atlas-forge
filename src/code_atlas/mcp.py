"""Servidor MCP sobre stdio — JSON-RPC 2.0 ndjson, zero-dep (lf-0042).

Transporte: newline-delimited JSON (MCP stdio). Sem HTTP neste ciclo.
Read-only: nenhuma tool destrutiva/escrita; inputs validados, limites
impostos. Delega aos motores reais — nunca stubs.

ADR de transporte: JSON-RPC próprio sobre stdio, mesmo padrão do
decoder SCIP (ADR-017) — o SDK oficial puxaria deps pesadas.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, TextIO

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "code-atlas-forge"

_ERR_METHOD = -32601
_ERR_PARAMS = -32602
_ERR_PARSE = -32700
_ERR_INTERNAL = -32603

_TOOLS: dict[str, dict[str, Any]] = {
    "atlas_search": {
        "description": "busca híbrida/lexical/graph/vector por símbolo",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string"},
            "strategy": {"type": "string", "enum": [
                "lexical", "structural", "graph", "hybrid", "vector"]},
            "root": {"type": "string"},
            "limit": {"type": "integer", "maximum": 200},
        }, "required": ["query"], "additionalProperties": False},
    },
    "atlas_symbol": {
        "description": "detalhe de um símbolo (qualified_name)",
        "inputSchema": {"type": "object", "properties": {
            "name": {"type": "string"}, "root": {"type": "string"}},
            "required": ["name"], "additionalProperties": False},
    },
    "atlas_references": {
        "description": "relações que tocam um símbolo (callers/callees)",
        "inputSchema": {"type": "object", "properties": {
            "name": {"type": "string"}, "root": {"type": "string"}},
            "required": ["name"], "additionalProperties": False},
    },
    "atlas_dependencies": {
        "description": "dependências comprovadas do snapshot",
        "inputSchema": {"type": "object", "properties": {
            "root": {"type": "string"},
            "relation_type": {"type": "string"}},
            "additionalProperties": False},
    },
    "atlas_context": {
        "description": "context pack sob budget (orientation|focus|deep)",
        "inputSchema": {"type": "object", "properties": {
            "task": {"type": "string"},
            "level": {"type": "string",
                      "enum": ["orientation", "focus", "deep"]},
            "budget": {"type": "integer", "maximum": 100000},
            "root": {"type": "string"}},
            "required": ["task"], "additionalProperties": False},
    },
    "atlas_evidence": {
        "description": "evidências + proveniência de uma aresta/símbolo",
        "inputSchema": {"type": "object", "properties": {
            "name": {"type": "string"}, "root": {"type": "string"}},
            "required": ["name"], "additionalProperties": False},
    },
    "atlas_status": {
        "description": "estado do repositório/snapshot/capabilities",
        "inputSchema": {"type": "object", "properties": {
            "root": {"type": "string"}}, "additionalProperties": False},
    },
    "atlas_expand": {
        "description": "expansão progressiva de símbolo no snapshot",
        "inputSchema": {"type": "object", "properties": {
            "name": {"type": "string"}, "root": {"type": "string"},
            "delivered": {"type": "string"}},
            "required": ["name"], "additionalProperties": False},
    },
    "atlas_impact": {
        "description": "análise de impacto entre revisões/snapshots",
        "inputSchema": {"type": "object", "properties": {
            "base": {"type": "string"}, "target": {"type": "string"},
            "symbol": {"type": "string"},
            "root": {"type": "string"},
            "known_only": {"type": "boolean"},
            "max_paths": {"type": "integer", "maximum": 200}},
            "additionalProperties": False},
    },
    "atlas_diff": {
        "description": "ChangeSet git read-only entre revisões",
        "inputSchema": {"type": "object", "properties": {
            "base": {"type": "string"}, "target": {"type": "string"},
            "root": {"type": "string"}},
            "required": ["base"], "additionalProperties": False},
    },
    "atlas_test_impact": {
        "description": "testes candidatos relacionados às mudanças",
        "inputSchema": {"type": "object", "properties": {
            "base": {"type": "string"}, "target": {"type": "string"},
            "root": {"type": "string"}},
            "additionalProperties": False},
    },
}

_RESOURCES = (
    ("atlas://repository", "repositório indexado"),
    ("atlas://snapshot", "snapshot READY atual"),
    ("atlas://capabilities", "capabilities do backend de busca"),
    ("atlas://graph", "resumo do grafo (counts por tipo de aresta)"),
    ("atlas://context", "context pack do snapshot (template)"),
    ("atlas://symbols", "símbolos do snapshot (primeira página)"),
)

_PROMPTS: dict[str, str] = {
    "understand-component": (
        "Entenda {component}: 1) atlas_search pelo nome; 2) "
        "atlas_symbol no melhor hit; 3) atlas_context com a tarefa; "
        "4) cite evidence e qualified_names reais — nunca invente."),
    "locate-implementation": (
        "Localize {name}: atlas_search estratégia hybrid; depois "
        "atlas_symbol no candidato; reporte path+span da ocorrência."),
    "explore-dependencies": (
        "Dependências de {symbol}: atlas_references + "
        "atlas_dependencies; distinga resolved/ambiguous/unresolved."),
    "prepare-safe-change": (
        "Antes de mudar {symbol}: atlas_context level=deep; liste "
        "impactados via atlas_references; preserve evidências."),
    "explain-code-path": (
        "Explique {path}: atlas_expand progressivo; cite arestas "
        "comprovadas; marque o que é inferido vs resolvido."),
}


@dataclass
class _Ctx:
    conn: sqlite3.Connection
    root: str
    repo_id: int
    snapshot_id: int


class AtlasMcpServer:
    """JSON-RPC 2.0 ndjson sobre stdio — read-only."""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path

    # ---------------------------------------------------- transporte
    def serve(self, in_f: TextIO, out_f: TextIO) -> int:
        for line in in_f:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                self._send(out_f, self._err(None, _ERR_PARSE,
                                            "JSON inválido"))
                continue
            resp = self._dispatch(msg)
            if resp is not None:
                self._send(out_f, resp)
        return 0

    def _send(self, out: TextIO, msg: dict[str, Any]) -> None:
        out.write(json.dumps(msg) + "\n")
        out.flush()

    def _err(self, rid: Any, code: int, msg: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": code, "message": msg}}

    def _ok(self, rid: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    # ---------------------------------------------------- dispatch
    def _dispatch(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        rid = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params") or {}
        if method == "initialize":
            return self._ok(rid, {
                "protocolVersion": params.get(
                    "protocolVersion", PROTOCOL_VERSION),
                "capabilities": {
                    "tools": {}, "resources": {}, "prompts": {}},
                "serverInfo": {"name": SERVER_NAME,
                               "version": self._version()}})
        if method == "notifications/initialized":
            return None
        if method == "ping":
            return self._ok(rid, {})
        if method == "tools/list":
            return self._ok(rid, {"tools": [
                {"name": n, **t} for n, t in sorted(_TOOLS.items())]})
        if method == "tools/call":
            return self._tools_call(rid, params)
        if method in ("resources/list", "resources/read",
                      "prompts/list", "prompts/get"):
            return self._dispatch_meta(rid, method, params)
        return self._err(rid, _ERR_METHOD, f"método desconhecido: {method}")

    def _dispatch_meta(
        self, rid: Any, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        if method == "resources/list":
            return self._ok(rid, {"resources": [
                {"uri": u, "name": n, "mimeType": "application/json"}
                for u, n in _RESOURCES]})
        if method == "resources/read":
            return self._resources_read(rid, params)
        if method == "prompts/list":
            return self._ok(rid, {"prompts": [
                {"name": n, "description": t.split(":")[0]}
                for n, t in sorted(_PROMPTS.items())]})
        if method == "prompts/get":
            name = params.get("name", "")
            if name not in _PROMPTS:
                return self._err(rid, _ERR_PARAMS, f"prompt '{name}'?")
            return self._ok(rid, {
                "description": name,
                "messages": [{"role": "user", "content": {
                    "type": "text", "text": _PROMPTS[name]}}]})
        return self._err(rid, _ERR_METHOD, f"método desconhecido: {method}")

    def _ctx(self, params: dict[str, Any]) -> _Ctx:
        from code_atlas.cli import _open_db
        from code_atlas.config import resolve_root
        from code_atlas.search.structural import StructuralSearch

        # lf-0054: `or "."` sombreava CODE_ATLAS_ROOT — env deve ser
        # consultada antes do cwd (resolve_root já faz essa cadeia).
        root = str(resolve_root(params.get("root")))
        conn = _open_db()
        conn.row_factory = sqlite3.Row
        st = StructuralSearch(conn)
        repo_id = st.repo_for_root(root)
        if repo_id is None:
            conn.close()
            raise LookupError(f"repositório não indexado: {root}")
        snap, err = st.resolve_snapshot(repo_id, None)
        if snap is None or snap.id is None:
            conn.close()
            raise LookupError(err or "nenhum snapshot READY")
        return _Ctx(conn, root, repo_id, int(snap.id))

    def _version(self) -> str:
        from code_atlas import __version__

        return __version__

    # ---------------------------------------------------- tools
    def _tools_call(
        self, rid: Any, params: dict[str, Any]
    ) -> dict[str, Any]:
        name = params.get("name", "")
        args = params.get("arguments") or {}
        if name not in _TOOLS:
            return self._err(rid, _ERR_PARAMS, f"tool '{name}'?")
        if not isinstance(args, dict):
            return self._err(rid, _ERR_PARAMS, "arguments deve ser objeto")
        try:
            data = self._run_tool(name, args)
        except LookupError as exc:
            return self._err(rid, _ERR_PARAMS, str(exc))
        except (ValueError, TypeError) as exc:
            return self._err(rid, _ERR_PARAMS, f"parâmetros: {exc}")
        except sqlite3.Error as exc:
            # lf-0054: erro de storage NUNCA pode derrubar o stdio server
            return self._err(rid, _ERR_INTERNAL, f"storage: {exc}")
        except Exception as exc:
            return self._err(rid, _ERR_INTERNAL, f"interno: {exc}")
        return self._ok(rid, {
            "content": [{"type": "text",
                         "text": json.dumps(data, default=str)}],
            "structuredContent": data})

    def _run_tool(self, name: str, args: dict[str, Any]) -> Any:
        from code_atlas.search import SearchEngine, SearchRequest

        ctx = self._ctx(args)
        try:
            if name == "atlas_search":
                res = SearchEngine(ctx.conn).search(SearchRequest(
                    query=str(args["query"]),
                    strategy=str(args.get("strategy", "hybrid")),
                    repository_root=ctx.root,
                    snapshot_id=ctx.snapshot_id,
                    limit=min(int(args.get("limit", 20)), 200)))
                return res.to_dict()
            if name == "atlas_context":
                from code_atlas.context import ContextEngine, ContextRequest

                pack = ContextEngine(ctx.conn).build(ContextRequest(
                    task=str(args["task"]),
                    repository_root=ctx.root,
                    snapshot_id=ctx.snapshot_id,
                    level=str(args.get("level", "focus")),  # type: ignore[arg-type]
                    budget_tokens=min(int(args.get("budget", 4000)),
                                      100000)))
                return pack.to_dict()
            if name == "atlas_expand":
                import dataclasses

                from code_atlas.context import ContextExpander

                exp = ContextExpander(ctx.conn).expand(
                    ctx.snapshot_id, str(args["name"]),
                    delivered=frozenset(
                        s for s in str(
                            args.get("delivered", "")).split(",") if s))
                return dataclasses.asdict(exp)
            if name == "atlas_status":

                return {
                    "root": ctx.root, "repo_id": ctx.repo_id,
                    "snapshot_id": ctx.snapshot_id,
                    "capabilities": dataclasses_asdict_safe(
                        SearchEngine(ctx.conn).capabilities()),
                }
            if name == "atlas_symbol":
                return self._symbol(ctx, str(args["name"]))
            if name == "atlas_references":
                return self._references(ctx, str(args["name"]))
            if name == "atlas_dependencies":
                return self._dependencies(
                    ctx, str(args.get("relation_type") or ""))
            if name == "atlas_evidence":
                return self._evidence(ctx, str(args["name"]))
            return self._run_impact_tool(ctx, name, args)
        finally:
            ctx.conn.close()
        raise LookupError(f"tool '{name}' não implementada")

    def _symbol(self, ctx: _Ctx, name: str) -> dict[str, Any]:
        rows = ctx.conn.execute(
            "SELECT s.qualified_name, s.kind, s.language, s.id"
            " FROM symbols s WHERE s.repository_id = ? AND"
            " (s.qualified_name = ? OR s.qualified_name LIKE ?)"
            " ORDER BY s.qualified_name LIMIT 20",
            (ctx.repo_id, name, f"%.{name}")).fetchall()
        return {"symbols": [dict(r) for r in rows],
                "snapshot_id": ctx.snapshot_id}

    def _references(self, ctx: _Ctx, name: str) -> dict[str, Any]:
        rows = ctx.conn.execute(
            "SELECT sa.qualified_name AS src, r.type,"
            " sb.qualified_name AS dst, r.resolution_status,"
            " r.resolver FROM relations r"
            " JOIN symbol_occurrences a ON a.id = r.from_id"
            " LEFT JOIN symbol_occurrences b ON b.id = r.to_id"
            " JOIN symbols sa ON sa.id = a.logical_symbol_id"
            " LEFT JOIN symbols sb ON sb.id = b.logical_symbol_id"
            " WHERE r.snapshot_id = ? AND (sa.qualified_name = ?"
            " OR sb.qualified_name = ?) LIMIT 100",
            (ctx.snapshot_id, name, name)).fetchall()
        return {"references": [dict(r) for r in rows],
                "snapshot_id": ctx.snapshot_id}

    def _dependencies(
        self, ctx: _Ctx, relation_type: str
    ) -> dict[str, Any]:
        base = (
            "SELECT sa.qualified_name AS src, r.type,"
            " sb.qualified_name AS dst FROM relations r"
            " JOIN symbol_occurrences a ON a.id = r.from_id"
            " LEFT JOIN symbol_occurrences b ON b.id = r.to_id"
            " JOIN symbols sa ON sa.id = a.logical_symbol_id"
            " LEFT JOIN symbols sb ON sb.id = b.logical_symbol_id"
            " WHERE r.snapshot_id = ?")
        params: list[Any] = [ctx.snapshot_id]
        if relation_type:
            sql = base + " AND r.type = ? LIMIT 200"
            params.append(relation_type)
        else:
            sql = base + " LIMIT 200"
        rows = ctx.conn.execute(sql, params).fetchall()
        return {"dependencies": [dict(r) for r in rows],
                "snapshot_id": ctx.snapshot_id}

    def _evidence(self, ctx: _Ctx, name: str) -> dict[str, Any]:
        # lf-0054: colunas reais da tabela evidence (path/span/hash +
        # proveniência v6) — e.kind/e.detail nunca existiram e o erro
        # não-tratado derrubava o processo do servidor.
        # lf-0051: evidência escopada ao símbolo pedido (arquivos com
        # ocorrências dele), nunca o snapshot inteiro sob o nome.
        rows = ctx.conn.execute(
            "SELECT e.id, e.path, e.span, e.content_hash, e.extractor,"
            " e.notes, e.resolver_id, e.evidence_tier,"
            " e.validation_status FROM evidence e"
            " WHERE e.snapshot_id = ? AND e.source_file_id IN ("
            "  SELECT DISTINCT o.source_file_id FROM symbol_occurrences o"
            "  JOIN symbols s ON s.id = o.logical_symbol_id"
            "  WHERE o.snapshot_id = ? AND s.qualified_name = ?)"
            " ORDER BY e.id LIMIT 200",
            (ctx.snapshot_id, ctx.snapshot_id, name)).fetchall()
        # refs canônicas grounded por occurrence do símbolo (lf-0051)
        from code_atlas.search.refs import EvidenceGrounder

        grounder = EvidenceGrounder(ctx.conn)
        occ_ids = [
            int(r["id"]) for r in ctx.conn.execute(
                "SELECT o.id FROM symbol_occurrences o JOIN symbols s"
                " ON s.id = o.logical_symbol_id"
                " WHERE o.snapshot_id = ? AND s.qualified_name = ?"
                " LIMIT 25",
                (ctx.snapshot_id, name)).fetchall()
        ]
        refs: list[dict[str, Any]] = []
        for oid in occ_ids:
            occ = grounder.occurrence_ref(ctx.repo_id, ctx.snapshot_id,
                                          oid)
            if occ is not None:
                refs.append({"kind": "syntactic-occurrence",
                             "repository_id": occ.repository_id,
                             "snapshot_id": occ.snapshot_id,
                             "occurrence_id": occ.occurrence_id,
                             "qualified_name": occ.qualified_name,
                             "path": occ.source_path,
                             "span": [occ.span_start, occ.span_end],
                             "content_hash": occ.content_hash})
        return {"evidence": [dict(r) for r in rows],
                "refs": refs,
                "snapshot_id": ctx.snapshot_id, "for": name}

    def _run_impact_tool(
        self, ctx: _Ctx, name: str, args: dict[str, Any],
    ) -> dict[str, Any]:
        """Despacho das tools de impacto (Cycle 5)."""
        if name in ("atlas_impact", "atlas_test_impact"):
            return self._impact(
                ctx, args, tests_only=name == "atlas_test_impact")
        if name == "atlas_diff":
            return self._diff(ctx, args)
        raise LookupError(f"tool '{name}' não implementada")

    def _impact(
        self, ctx: _Ctx, args: dict[str, Any], *, tests_only: bool,
    ) -> dict[str, Any]:
        """atlas_impact/atlas_test_impact — mesmo ImpactEngine da CLI."""
        from code_atlas.impact.contracts import (
            ImpactLimits,
            ImpactRequest,
        )
        from code_atlas.impact.engine import ImpactEngine
        from code_atlas.impact.report import _jsonable

        req = ImpactRequest(
            repository_root=ctx.root,
            base_revision=str(args.get("base") or ""),
            target_revision=str(args.get("target") or ""),
            symbol=str(args["symbol"]) if args.get("symbol") else None,
            known_only=bool(args.get("known_only")),
            limits=ImpactLimits(
                max_paths=min(200, int(args.get("max_paths") or 50))))
        res = ImpactEngine(ctx.conn).analyze(req)
        if tests_only:
            res = replace(res, symbol_changes=(), contract_changes=(),
                          impacts=())
        return _jsonable(res)  # type: ignore[return-value]

    def _diff(
        self, ctx: _Ctx, args: dict[str, Any],
    ) -> dict[str, Any]:
        """atlas_diff — ChangeSet git read-only (sem tocar workspace)."""
        from code_atlas.impact import diff_reader
        from code_atlas.impact.report import _jsonable

        cs, diags = diff_reader.read_diff(
            Path(ctx.root), str(args["base"]),
            str(args["target"]) if args.get("target") else None)
        return {
            "change_set": _jsonable(cs),
            "diagnostics": [_jsonable(d) for d in diags],
            "snapshot_id": ctx.snapshot_id}

    # ---------------------------------------------------- resources
    def _resources_read(
        self, rid: Any, params: dict[str, Any]
    ) -> dict[str, Any]:
        uri = str(params.get("uri", ""))
        try:
            ctx = self._ctx({})
        except LookupError as exc:
            return self._err(rid, _ERR_PARAMS, str(exc))
        try:
            if uri == "atlas://repository":
                data: Any = {"root": ctx.root, "repo_id": ctx.repo_id}
            elif uri == "atlas://snapshot":
                data = {"snapshot_id": ctx.snapshot_id}
            elif uri == "atlas://capabilities":
                import dataclasses

                from code_atlas.search import SearchEngine

                data = dataclasses.asdict(
                    SearchEngine(ctx.conn).capabilities())
            elif uri == "atlas://graph":
                row = ctx.conn.execute(
                    "SELECT type, COUNT(*) AS c FROM relations"
                    " WHERE snapshot_id = ? GROUP BY type",
                    (ctx.snapshot_id,)).fetchall()
                data = {"edges_by_type": {r["type"]: r["c"]
                                          for r in row},
                        "snapshot_id": ctx.snapshot_id}
            elif uri == "atlas://symbols":
                rows = ctx.conn.execute(
                    "SELECT qualified_name, kind FROM symbols"
                    " WHERE repository_id = ? ORDER BY qualified_name"
                    " LIMIT 100", (ctx.repo_id,)).fetchall()
                data = {"symbols": [dict(r) for r in rows]}
            elif uri == "atlas://context":
                data = {"hint": "use tools/call atlas_context",
                        "snapshot_id": ctx.snapshot_id}
            else:
                return self._err(rid, _ERR_PARAMS,
                                 f"uri desconhecida: {uri}")
        finally:
            ctx.conn.close()
        return self._ok(rid, {"contents": [{
            "uri": uri, "mimeType": "application/json",
            "text": json.dumps(data, default=str)}]})


def dataclasses_asdict_safe(obj: Any) -> dict[str, Any]:
    import dataclasses

    return dataclasses.asdict(obj)


def main_stdio(db_path: str | None = None) -> int:
    """Entrypoint `code-atlas mcp` — stdio ndjson."""
    return AtlasMcpServer(db_path).serve(sys.stdin, sys.stdout)
