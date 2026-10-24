"""lf-0054 — interoperabilidade MCP com cliente REAL (SDK oficial `mcp`).

Spawna ``python -m code_atlas mcp`` como subprocesso stdio e exercita
o handshake via ``mcp.client.stdio``: initialize, tools/list,
tools/call, resources/list|read, prompts/list|get e erro de método.

``CODE_ATLAS_DATA_DIR`` isola o banco do fixture. Sem o SDK no
ambiente o módulo inteiro é skipped — NOT RUN honesto, nunca fake.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "golden"))

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("mcp") is None,
    reason="SDK mcp ausente — E2E real marcado NOT RUN")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    from retrieval_eval import build_eval_repo
    root = tmp_path / "repo"
    conn = build_eval_repo(root)  # grava atlas.db em tmp_path
    conn.close()
    return root


def _server_params(tmp_path: Path, repo: Path):
    from mcp import StdioServerParameters
    env = dict(os.environ)
    env["CODE_ATLAS_DATA_DIR"] = str(tmp_path)
    env["CODE_ATLAS_CONFIG_DIR"] = str(tmp_path / "cfg")
    # root ambiente — resources/read e prompts são workspace-scoped
    env["CODE_ATLAS_ROOT"] = str(repo)
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "code_atlas", "mcp"], env=env)


def _payload(call) -> dict:
    assert call.content, "resposta sem conteúdo"
    return json.loads(call.content[0].text)


async def _session(repo: Path, tmp_path: Path, fn):
    from mcp.client.stdio import stdio_client

    from mcp import ClientSession
    async with stdio_client(_server_params(tmp_path, repo)) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        return await fn(s)


def test_real_client_handshake_and_tools(repo: Path, tmp_path: Path):
    async def run(s):
        init = await s.initialize()
        assert init.server_info.name == "code-atlas-forge"
        tools = await s.list_tools()
        names = {t.name for t in tools.tools}
        assert names == {
            "atlas_context", "atlas_dependencies", "atlas_evidence",
            "atlas_expand", "atlas_references", "atlas_search",
            "atlas_status", "atlas_symbol",
            "atlas_impact", "atlas_diff", "atlas_test_impact"}
        status = _payload(await s.call_tool(
            "atlas_status", {"root": str(repo)}))
        assert status["repo_id"] > 0
        return status
    status = asyncio.run(_session(repo, tmp_path, run))
    assert status["snapshot_id"] > 0


def test_real_client_search_and_symbol(repo: Path, tmp_path: Path):
    async def run(s):
        res = _payload(await s.call_tool(
            "atlas_search",
            {"query": "charge", "root": str(repo), "limit": 5}))
        assert res["items"], "busca real deve retornar hits"
        sym = _payload(await s.call_tool(
            "atlas_symbol",
            {"name": res["items"][0]["qualified_name"],
             "root": str(repo)}))
        return res, sym
    res, sym = asyncio.run(_session(repo, tmp_path, run))
    assert sym["snapshot_id"] == res["snapshot_id"]


def test_real_client_context_and_evidence(repo: Path, tmp_path: Path):
    async def run(s):
        ctx = _payload(await s.call_tool(
            "atlas_context",
            {"task": "orders", "root": str(repo),
             "budget_tokens": 2000}))
        assert ctx["manifest"]["snapshot_id"] > 0
        ev = _payload(await s.call_tool(
            "atlas_evidence",
            {"name": "pkg.orders.OrderService.create_order",
             "root": str(repo)}))
        return ctx, ev
    ctx, ev = asyncio.run(_session(repo, tmp_path, run))
    # lf-0051: refs canônicas — nunca fabricadas
    assert all(r["snapshot_id"] == ctx["manifest"]["snapshot_id"]
               or "occurrence_id" in r for r in ev.get("refs", ()))


def test_real_client_resources_prompts_and_error(
        repo: Path, tmp_path: Path):
    async def run(s):
        resources = await s.list_resources()
        assert resources.resources
        rd = await s.read_resource(resources.resources[0].uri)
        assert rd.contents
        prompts = await s.list_prompts()
        assert prompts.prompts
        pg = await s.get_prompt(
            prompts.prompts[0].name, {"root": str(repo)})
        assert pg.messages
        # erro de tool desconhecida — JSON-RPC error real
        from mcp.shared.exceptions import MCPError
        with pytest.raises(MCPError):
            await s.call_tool("atlas_nonexistent", {"root": str(repo)})
        # repo inexistente — erro tipado via JSON-RPC (não isError)
        with pytest.raises(MCPError) as exc:
            await s.call_tool(
                "atlas_status", {"root": str(tmp_path / "nope")})
        return exc
    exc = asyncio.run(_session(repo, tmp_path, run))
    assert "não indexado" in str(exc.value)


def test_real_client_shutdown_clean(repo: Path, tmp_path: Path):
    async def run(s):
        await s.initialize()
        return True
    assert asyncio.run(_session(repo, tmp_path, run)) is True
