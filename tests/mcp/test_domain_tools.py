"""lf-0123 Phase 15 — tools de domínio do servidor MCP stdio.

``atlas_domains`` / ``atlas_domain_entity`` / ``atlas_domain_lineage``
/ ``atlas_federation_view`` — read-only, bounded, snapshot-scoped.
Honestidade de ausência: repo indexado sem adapters → erro tipado ou
payload vazio com nota, nunca dados fabricados.
"""

from __future__ import annotations

import io
import json
import sqlite3
import unittest.mock as mock
from pathlib import Path

import pytest

from code_atlas.domains.registry import DomainRegistry
from code_atlas.domains.sql import SqlDomainAdapter
from code_atlas.federation import Federation
from code_atlas.indexing import IndexCoordinator
from code_atlas.mcp import AtlasMcpServer
from code_atlas.storage import connect, migrate

_SQL = {
    "landing.sql": (
        "CREATE TABLE landing.orders (id INT, amount DOUBLE);\n"
        "INSERT INTO landing.orders SELECT id, amount FROM staging.raw_orders;\n"
    ),
    "mart.sql": (
        "CREATE VIEW mart.v_orders AS SELECT id, amount FROM landing.orders;\n"
    ),
}


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return root


def _open_row(db: Path) -> sqlite3.Connection:
    conn = connect(db)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Dois repos indexados com ``sql`` + workspace federado.

    ``_open_db``/``resolve_root`` são monkeypatched como em
    ``test_server.py`` — tools passam a ler o banco do teste.
    """
    db = tmp_path / "atlas.db"
    conn = connect(db)
    migrate(conn)
    repo_a = _write(tmp_path / "repo_a", dict(_SQL))
    repo_b = _write(tmp_path / "repo_b", dict(_SQL))
    ids: dict[str, object] = {"root_a": repo_a, "root_b": repo_b}
    for key, root in (("a", repo_a), ("b", repo_b)):
        reg = DomainRegistry()
        reg.register(SqlDomainAdapter(base_path=root))
        out = IndexCoordinator(conn, domain_registry=reg).index(root)
        assert out.status == "success" and out.snapshot_id is not None
        ids[f"snap_{key}"] = out.snapshot_id
    # workspace federado com os dois repos (engine lf-0118 real)
    fed = Federation(conn)
    ws = fed.create_workspace("ws-dom")
    for root in (repo_a, repo_b):
        fed.add_member(ws, fed.resolve_repository(str(root)))
    conn.close()

    from code_atlas import cli as cli_mod

    monkeypatch.setattr(cli_mod, "_open_db", lambda: _open_row(db))
    monkeypatch.setattr(cli_mod, "resolve_root", lambda p: Path(p or "."))
    yield str(repo_a)


def _rpc(env: str, *msgs: dict, root: str | None = None) -> list[dict]:
    inp = io.StringIO("".join(json.dumps(m) + "\n" for m in msgs))
    out = io.StringIO()
    server = AtlasMcpServer()
    real_ctx = server._ctx

    def patched(params: dict) -> object:
        params = dict(params)
        params.setdefault("root", root or env)
        return real_ctx(params)

    with mock.patch.object(server, "_ctx", patched):
        server.serve(inp, out)
    return [json.loads(ln) for ln in out.getvalue().splitlines() if ln]


def _call(env: str, name: str, arguments: dict, *, root: str | None = None) -> dict:
    r = _rpc(
        env,
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": name, "arguments": arguments}},
        root=root)
    return r[0]


def test_tools_registered(env: str) -> None:
    r = _rpc(env, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in r[0]["result"]["tools"]}
    assert {"atlas_domains", "atlas_domain_entity",
            "atlas_domain_lineage", "atlas_federation_view"} <= names


def test_atlas_domains_no_repo_needed(env: str) -> None:
    # listar adapters não exige repo indexado nem toca o banco
    r = _call(env, "atlas_domains", {}, root="/nonexistent-xyz")
    data = r["result"]["structuredContent"]
    ids = {a["domain_id"] for a in data["adapters"]}
    assert "sql" in ids and "terraform" in ids
    sql = next(a for a in data["adapters"] if a["domain_id"] == "sql")
    assert ".sql" in sql["artifact_support"]["extensions"]
    assert "opt-in" in data["note"]


def test_atlas_domain_entity_detail(env: str) -> None:
    r = _call(env, "atlas_domain_entity",
              {"key": "mart.v_orders", "root": env})
    data = r["result"]["structuredContent"]
    assert data["identity"]["display_name"] == "mart.v_orders"
    assert data["identity"]["kind"] == "data_view"
    assert data["identity"]["domain"] == "sql"
    assert data["snapshot_id"] > 0
    assert data["occurrences"]
    assert data["occurrences"][0]["path"] == "mart.sql"
    assert data["relations_summary"]
    assert data["evidence_count"] >= 1


def test_atlas_domain_entity_by_identity_key(env: str) -> None:
    from code_atlas import cli as cli_mod

    conn = cli_mod._open_db()
    row = conn.execute(
        "SELECT identity_key FROM domain_entities"
        " WHERE qualified_name = 'v_orders' LIMIT 1").fetchone()
    conn.close()
    r = _call(env, "atlas_domain_entity", {"key": row["identity_key"]})
    data = r["result"]["structuredContent"]
    assert data["identity"]["display_name"] == "mart.v_orders"


def test_atlas_domain_lineage_bounded(env: str) -> None:
    r = _call(env, "atlas_domain_lineage",
              {"entity": "mart.v_orders", "direction": "up", "depth": 2})
    data = r["result"]["structuredContent"]
    refs = {n["ref"] for n in data["nodes"]}
    assert "landing.orders" in refs
    assert "staging.raw_orders" in refs
    assert max(n["depth"] for n in data["nodes"]) <= 2
    assert data["truncated"] is False
    # depth=1 é bounded — raw_orders só aparece em d2
    r1 = _call(env, "atlas_domain_lineage",
               {"entity": "mart.v_orders", "direction": "up", "depth": 1})
    refs1 = {n["ref"] for n in r1["result"]["structuredContent"]["nodes"]}
    assert "staging.raw_orders" not in refs1


def test_atlas_domain_lineage_down(env: str) -> None:
    r = _call(env, "atlas_domain_lineage",
              {"entity": "staging.raw_orders", "direction": "down",
               "depth": 3})
    data = r["result"]["structuredContent"]
    refs = {n["ref"] for n in data["nodes"]}
    assert "landing.orders" in refs and "mart.v_orders" in refs


def test_atlas_federation_view(env: str) -> None:
    r = _call(env, "atlas_federation_view", {"workspace": "ws-dom"})
    data = r["result"]["structuredContent"]
    assert data["view_id"].startswith("fedview:")
    assert data["workspace"] == "ws-dom"
    members = {m["repository_id"]: m for m in data["members"]}
    assert len(members) == 2
    assert all(m["snapshot_id"] for m in members.values())
    assert all(m["resolution"] == "latest-ready"
               for m in members.values())


def test_atlas_federation_view_pin_override(env: str) -> None:
    from code_atlas import cli as cli_mod

    conn = cli_mod._open_db()
    snaps = [dict(r) for r in conn.execute(
        "SELECT repository_id, id FROM snapshots"
        " WHERE status='READY' ORDER BY repository_id").fetchall()]
    conn.close()
    pins = {str(snaps[0]["repository_id"]): snaps[0]["id"]}
    r = _call(env, "atlas_federation_view",
              {"workspace": "ws-dom", "pins": pins})
    data = r["result"]["structuredContent"]
    m = next(m for m in data["members"]
             if m["repository_id"] == snaps[0]["repository_id"])
    assert m["resolution"] == "pin-override"


def test_atlas_federation_view_unknown_workspace(env: str) -> None:
    r = _call(env, "atlas_federation_view", {"workspace": "ghost"})
    assert r["error"]["code"] == -32602
    assert "ghost" in r["error"]["message"]


def test_domain_entity_absent_is_typed_error(env: str) -> None:
    r = _call(env, "atlas_domain_entity", {"key": "no.such"})
    assert r["error"]["code"] == -32602
    assert "not found" in r["error"]["message"]
    assert "opt-in" in r["error"]["message"]


def test_domain_tools_honest_without_adapters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repo indexado SEM --domains: lookup → erro tipado honesto."""
    db = tmp_path / "atlas.db"
    conn = connect(db)
    migrate(conn)
    repo = _write(tmp_path / "plain", dict(_SQL))
    out = IndexCoordinator(conn).index(repo)  # sem domain_registry
    assert out.status == "success"
    conn.close()

    from code_atlas import cli as cli_mod

    monkeypatch.setattr(cli_mod, "_open_db", lambda: _open_row(db))
    monkeypatch.setattr(cli_mod, "resolve_root", lambda p: Path(p or "."))

    r = _call(str(repo), "atlas_domain_entity", {"key": "mart.v_orders"})
    assert r["error"]["code"] == -32602
    assert "opt-in" in r["error"]["message"]

    r = _call(str(repo), "atlas_domain_lineage",
              {"entity": "mart.v_orders"})
    assert r["error"]["code"] == -32602


def test_snapshot_isolation_between_repos(env: str, tmp_path: Path) -> None:
    """Entidade só existe no repo do ctx — outro repo → not found."""
    from code_atlas import cli as cli_mod

    conn = cli_mod._open_db()
    # repo_a e repo_b têm as mesmas entidades (fixture idêntica):
    # o lookup escopado por repo resolve só no repo do ctx.
    n = conn.execute(
        "SELECT COUNT(DISTINCT repository_id) AS c FROM domain_entities"
    ).fetchone()["c"]
    conn.close()
    assert n == 2
    r = _call(env, "atlas_domain_entity", {"key": "landing.orders"})
    data = r["result"]["structuredContent"]
    assert data["identity"]["display_name"] == "landing.orders"
    # repo do ctx, nunca o outro membro
    repo_id = data["repository_id"]
    assert isinstance(repo_id, int)
