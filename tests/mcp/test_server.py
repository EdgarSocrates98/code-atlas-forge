"""lf-0042 — servidor MCP stdio: handshake, tools reais, resources,
prompts, erros tipados. Cliente fake via StringIO."""

from __future__ import annotations

import io
import json
import sqlite3
import unittest.mock as mock
from pathlib import Path

import pytest

from code_atlas.indexing import IndexCoordinator
from code_atlas.mcp import AtlasMcpServer
from code_atlas.storage import connect, migrate

_REPO = {
    "pkg/util.py": "def helper():\n    return 1\n",
    "pkg/engine.py": (
        "from pkg.util import helper\n\ndef run():\n    return helper()\n"
    ),
}


@pytest.fixture()
def env(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    for rel, c in _REPO.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(c)
    db = tmp_path / "atlas.db"
    conn = connect(db)
    migrate(conn)
    IndexCoordinator(conn).index(repo)
    conn.close()

    from code_atlas import cli as cli_mod

    monkeypatch.setattr(
        cli_mod, "_open_db",
        lambda: _open_row(db))
    monkeypatch.setattr(
        cli_mod, "resolve_root", lambda p: Path(p))
    yield str(repo)


def _open_row(db: Path) -> sqlite3.Connection:
    conn = connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def _rpc(env: str, *msgs: dict) -> list[dict]:
    inp = io.StringIO("".join(json.dumps(m) + "\n" for m in msgs))
    out = io.StringIO()
    server = AtlasMcpServer()
    real_ctx = server._ctx

    def patched(params):
        params = dict(params)
        params.setdefault("root", env)
        return real_ctx(params)

    with mock.patch.object(server, "_ctx", patched):
        server.serve(inp, out)
    return [json.loads(ln) for ln in out.getvalue().splitlines() if ln]


def test_initialize_and_version_negotiation(env) -> None:
    r = _rpc(env, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                   "params": {"protocolVersion": "2024-11-05"}})
    assert r[0]["result"]["protocolVersion"] == "2024-11-05"
    assert r[0]["result"]["serverInfo"]["name"] == "code-atlas-forge"


def test_tools_list_and_search_real(env) -> None:
    r = _rpc(
        env,
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "atlas_search",
                    "arguments": {"query": "helper", "root": env}}})
    names = {t["name"] for t in r[0]["result"]["tools"]}
    assert {"atlas_search", "atlas_context", "atlas_expand",
            "atlas_status", "atlas_evidence", "atlas_symbol",
            "atlas_references", "atlas_dependencies"} <= names
    data = r[1]["result"]["structuredContent"]
    assert data["strategy"] == "hybrid"
    assert any("helper" in i["qualified_name"] for i in data["items"])


def test_context_and_expand_tools(env) -> None:
    r = _rpc(
        env,
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "atlas_context",
                    "arguments": {"task": "helper", "budget": 2000,
                                  "root": env}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "atlas_expand",
                    "arguments": {"name": "pkg.util.helper",
                                  "root": env}}})
    pack = r[0]["result"]["structuredContent"]
    assert pack["manifest"]["level"] == "focus"
    exp = r[1]["result"]["structuredContent"]
    assert exp["error"] == "" and exp["snapshot_id"] > 0


def test_resources_and_prompts(env) -> None:
    r = _rpc(
        env,
        {"jsonrpc": "2.0", "id": 1, "method": "resources/list"},
        {"jsonrpc": "2.0", "id": 2, "method": "resources/read",
         "params": {"uri": "atlas://capabilities"}},
        {"jsonrpc": "2.0", "id": 3, "method": "prompts/list"},
        {"jsonrpc": "2.0", "id": 4, "method": "prompts/get",
         "params": {"name": "prepare-safe-change"}})
    uris = {x["uri"] for x in r[0]["result"]["resources"]}
    assert "atlas://capabilities" in uris
    caps = json.loads(r[1]["result"]["contents"][0]["text"])
    assert "strategies" in caps
    assert len(r[2]["result"]["prompts"]) == 5
    assert "atlas_context" in (
        r[3]["result"]["messages"][0]["content"]["text"])


def test_typed_errors(env) -> None:
    r = _rpc(
        env,
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "atlas_nonexistent", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 2, "method": "bogus/method"},
        {"jsonrpc": "2.0", "id": 3, "method": "resources/read",
         "params": {"uri": "atlas://bogus"}})
    assert r[0]["error"]["code"] == -32602
    assert r[1]["error"]["code"] == -32601
    assert r[2]["error"]["code"] == -32602


def test_status_tool(env) -> None:
    r = _rpc(env, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": "atlas_status",
                              "arguments": {"root": env}}})
    d = r[0]["result"]["structuredContent"]
    assert d["snapshot_id"] > 0
    assert "strategies" in d["capabilities"]
