"""lf-0155 (Cycle 8.1 Phase 12) — manifest da superfície pública 1.0.

Snapshot declarativo dos nomes públicos estáveis desde o RC:
subcomandos do CLI, tools MCP e rotas HTTP — mais os endpoints de
versão. O manifesto pega **remoções acidentais** (qualquer nome que
sumir falha o teste) e delibera adições (extra exige atualizar o
manifesto — mudança de superfície nunca é silenciosa).

Medido de verdade: os nomes são resolvidos pela própria máquina de
despacho (argparse ``build_parser``, ``tools/list`` do MCP,
``handlers.handle`` das rotas) — não por strings em docs.
"""

from __future__ import annotations

import argparse
import io
import sqlite3
from typing import ClassVar

import pytest

from code_atlas import __version__
from code_atlas.api import handlers as api_handlers
from code_atlas.api.server import ApiError
from code_atlas.cli import build_parser, main
from code_atlas.mcp import _TOOLS, AtlasMcpServer
from code_atlas.storage.db import fts5_available
from code_atlas.storage.migrations import migrate, migrations, schema_version

EXPECTED_VERSION = "1.0.0"

#: Subcomandos top-level do CLI — todos devem continuar existindo.
CLI_COMMANDS = frozenset({
    "doctor", "init", "index", "semantic-status", "index-plan",
    "status", "search", "context", "context-expand", "mcp",
    "symbol", "references", "graph", "definitions", "dependencies",
    "impact", "api", "explorer", "federation", "domains",
})

#: Choices posicionais dentro de subcomandos agrupadores.
CLI_SUBACTIONS: dict[str, tuple[str, frozenset[str]]] = {
    "impact": ("cmd", frozenset(
        {"diff", "worktree", "symbol", "tests", "contract", "report"})),
    "mcp": ("cmd", frozenset({"serve", "doctor", "manifest"})),
    "federation": ("fed_cmd", frozenset(
        {"create", "add", "pin", "view"})),
    "domains": ("dom_cmd", frozenset(
        {"list", "entities", "entity", "lineage"})),
}

#: Tools MCP expostas — o contrato do servidor (15 tools no RC).
MCP_TOOLS = frozenset({
    "atlas_context", "atlas_dependencies", "atlas_diff",
    "atlas_domain_entity", "atlas_domain_lineage", "atlas_domains",
    "atlas_evidence", "atlas_expand", "atlas_federation_view",
    "atlas_impact", "atlas_references", "atlas_search",
    "atlas_status", "atlas_symbol", "atlas_test_impact",
})

#: Rotas GET — as URLs concretas do despacho em handlers.handle.
API_GET_ROUTES = frozenset({
    "/health", "/capabilities", "/diagnostics", "/repositories",
    "/graph/overview", "/graph/neighbors", "/search", "/index/status",
    "/domains", "/domains/entities", "/domains/relations",
    "/domains/lineage",
    "/repositories/1/snapshots", "/symbols/1", "/evidence/1",
    "/files/1/occurrences", "/domains/entities/1/neighbors",
    "/federation/workspaces",
    "/federation/workspaces/1/view",
    "/federation/workspaces/1/entities/x/occurrences",
    "/federation/workspaces/1/graph",
    "/federation/workspaces/1/search",
    "/federation/workspaces/1",
})

API_POST_ROUTES = frozenset({"/context", "/impact/analyze"})


def _subparser_action(parser: argparse.ArgumentParser):
    return next(
        a for a in parser._actions
        if isinstance(a, argparse._SubParsersAction))


class _StubServer:
    """Mínimo para ``handlers.handle``: conn fresca por chamada."""

    db_path = ":memory:"
    started_at = None

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        migrate(conn)
        return conn


class _StubHandler:
    """Mínimo de ``BaseHTTPRequestHandler`` usado pelo despacho."""

    path = "/x"
    headers: ClassVar[dict[str, str]] = {}
    rfile = io.BytesIO(b"")


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


def test_cli_command_manifest() -> None:
    """Todos os subcomandos do RC existem; nenhum nasceu sem manifesto."""
    parser = build_parser()
    subs = _subparser_action(parser)
    commands = set(subs.choices)
    missing = CLI_COMMANDS - commands
    extra = commands - CLI_COMMANDS
    assert not missing, f"subcomandos removidos: {sorted(missing)}"
    assert not extra, (
        f"subcomandos novos fora do manifesto: {sorted(extra)}")


@pytest.mark.parametrize("cmd", sorted(CLI_COMMANDS))
def test_cli_subcommand_help(cmd: str) -> None:
    """``<cmd> --help`` resolve — superfície parseável e documentada."""
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args([cmd, "--help"])
    assert exc.value.code == 0


def test_cli_grouped_subactions_preserved() -> None:
    """Choices internos dos agrupadores (impact/mcp/federation/domains)."""
    subs = _subparser_action(build_parser())
    for cmd, (dest, expected) in CLI_SUBACTIONS.items():
        action = next(
            a for a in subs.choices[cmd]._actions
            if getattr(a, "dest", None) == dest)
        got = set(action.choices or ())
        assert expected <= got, (
            f"{cmd} {dest}: choices removidos "
            f"{sorted(expected - got)}")


def test_cli_version_flag(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert f"code-atlas {EXPECTED_VERSION}" in out


# ---------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------


def test_mcp_tool_manifest() -> None:
    tools = set(_TOOLS)
    missing = MCP_TOOLS - tools
    extra = tools - MCP_TOOLS
    assert not missing, f"tools MCP removidas: {sorted(missing)}"
    assert not extra, f"tools MCP fora do manifesto: {sorted(extra)}"
    for name, spec in _TOOLS.items():
        assert spec["description"].strip(), name
        assert spec["inputSchema"]["type"] == "object", name


def test_mcp_tools_list_matches_registry() -> None:
    """``tools/list`` do protocolo expõe exatamente o registro."""
    resp = AtlasMcpServer()._dispatch(
        {"id": 1, "method": "tools/list", "params": {}})
    names = {t["name"] for t in resp["result"]["tools"]}
    assert names == MCP_TOOLS


def test_mcp_server_version() -> None:
    resp = AtlasMcpServer()._dispatch(
        {"id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05"}})
    info = resp["result"]["serverInfo"]
    assert info["version"] == EXPECTED_VERSION == __version__


# ---------------------------------------------------------------------
# API HTTP — despacho real em handlers.handle
# ---------------------------------------------------------------------


@pytest.mark.parametrize("route", sorted(API_GET_ROUTES))
def test_api_get_route_exists(route: str) -> None:
    """A rota despacha para um handler real — ``not-found`` = remoção.

    Erros tipados de domínio (repo-not-found, bad-param…) provam a
    fiação; só ``not-found`` (ou exceção não-ApiError) é falha.
    """
    try:
        api_handlers.handle(_StubServer(), "GET", route, _StubHandler())
    except ApiError as exc:
        assert exc.code != "not-found", (
            f"rota GET {route} não existe mais")


@pytest.mark.parametrize("route", sorted(API_POST_ROUTES))
def test_api_post_route_exists(route: str) -> None:
    try:
        api_handlers.handle(_StubServer(), "POST", route, _StubHandler())
    except ApiError as exc:
        assert exc.code != "not-found", (
            f"rota POST {route} não existe mais")


def test_api_unknown_route_fails_honestly() -> None:
    """Controle do manifesto: rota inexistente → not-found tipado."""
    with pytest.raises(ApiError) as exc:
        api_handlers.handle(
            _StubServer(), "GET", "/rota-inexistente", _StubHandler())
    assert exc.value.code == "not-found"


# ---------------------------------------------------------------------
# versão — endpoints reportam a versão do pacote
# ---------------------------------------------------------------------


def test_version_endpoints_report_package_version(tmp_path) -> None:
    srv = _StubServer()
    srv.db_path = str(tmp_path / "atlas.db")
    health = api_handlers.handle(
        srv, "GET", "/health", _StubHandler())
    assert health["status"] == "ok"
    assert health["version"] == 1  # versão do protocolo da API
    diag = api_handlers.handle(
        srv, "GET", "/diagnostics", _StubHandler())
    assert diag["version"] == EXPECTED_VERSION == __version__
    assert diag["api_version"] == 1
    # schema_version = head real das migrações desta build
    conn = sqlite3.connect(":memory:")
    head = len(migrations(fts5=fts5_available(conn)))
    conn.close()
    assert diag["schema_version"] == head


def test_package_version_is_the_rc() -> None:
    """O valor autoritativo (pyproject attr=code_atlas.__version__)."""
    assert __version__ == EXPECTED_VERSION


def test_schema_version_matches_migration_head() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    assert schema_version(conn) == 0
    head = len(migrations(fts5=fts5_available(conn)))
    migrate(conn)
    assert schema_version(conn) == head
    conn.close()
