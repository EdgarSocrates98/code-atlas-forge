"""Install-2.1 — workspace scope, delegation surface, per-target receipts.

Isolation is env-based: CODE_ATLAS_DATA_DIR / CODE_ATLAS_CONFIG_DIR /
CODE_ATLAS_ROOT point at tmp dirs, so nothing touches the real profile.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_atlas import cli
from code_atlas.install import lifecycle
from code_atlas.install.workspace import discover_projects


@pytest.fixture()
def env(monkeypatch, tmp_path):
    data = tmp_path / "data"
    conf = tmp_path / "conf"
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("CODE_ATLAS_DATA_DIR", str(data))
    monkeypatch.setenv("CODE_ATLAS_CONFIG_DIR", str(conf))
    monkeypatch.setenv("CODE_ATLAS_ROOT", str(ws))
    return {"data": data, "conf": conf, "ws": ws}


def _receipt_file(env) -> Path:
    return env["data"] / "install" / "receipt.json"


def _make_member(ws: Path, name: str, marker: str) -> Path:
    m = ws / name
    (m / marker).mkdir(parents=True) if marker == ".git" else None
    if marker != ".git":
        m.mkdir(parents=True, exist_ok=True)
        (m / marker).write_text("{}\n", encoding="utf-8")
    return m


# ------------------------------------------------------------ discovery


def test_discover_projects_git_and_gitless(env) -> None:
    ws = env["ws"]
    git_m = _make_member(ws, "backend", ".git")
    py_m = _make_member(ws, "lib", "pyproject.toml")
    js_m = _make_member(ws, "web", "package.json")
    (ws / "plain").mkdir()  # no marker — not a member
    (ws / "plain" / "deep").mkdir()
    found = discover_projects(ws)
    assert found == sorted([git_m, py_m, js_m], key=str)


def test_discover_projects_includes_root_with_marker(env) -> None:
    ws = env["ws"]
    (ws / "pyproject.toml").write_text("[project]\nname='ws'\n", encoding="utf-8")
    found = discover_projects(ws)
    assert ws.resolve() in found


def test_discover_projects_never_descends_into_member(env) -> None:
    ws = env["ws"]
    outer = _make_member(ws, "outer", "pyproject.toml")
    inner = _make_member(outer, "inner", "pyproject.toml")
    found = discover_projects(ws)
    assert outer.resolve() in found
    assert inner.resolve() not in found  # nested member stays isolated


def test_discover_projects_skips_hidden_ignored_and_symlinks(env, tmp_path) -> None:
    ws = env["ws"]
    hidden = _make_member(ws, ".hidden", "pyproject.toml")
    ignored = _make_member(ws, "node_modules", "pyproject.toml")
    found = discover_projects(ws)
    assert hidden.resolve() not in found
    assert ignored.resolve() not in found
    outside = tmp_path / "outside"
    _make_member(outside, "ext", "pyproject.toml")
    try:
        (ws / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    found = discover_projects(ws)
    assert not any("ext" in str(f) for f in found)  # external link not followed


# ------------------------------------------------------------ workspace install


def test_workspace_scope_registers_members_and_federation(env) -> None:
    ws = env["ws"]
    _make_member(ws, "api", "pyproject.toml")
    _make_member(ws, "worker", "package.json")
    _make_member(ws, "repo", ".git")
    doc = lifecycle.install(scope="workspace", yes=True)
    assert doc["schema"] == "forge/InstallReceipt/v1"
    assert doc["operation"] == "install"
    assert doc["scope"] == "workspace"
    assert doc["status"] == "completed"
    assert doc["verification"]["status"] == "PASS"
    assert doc["workspace"]["members_discovered"] == 3
    statuses = {m["name"]: m["status"] for m in doc["members"]}
    assert statuses == {"api": "registered", "worker": "registered", "repo": "registered"}
    assert all(m["identity_key"] for m in doc["members"])
    assert len({m["repository_id"] for m in doc["members"]}) == 3
    # federation group named by the workspace identity — stable
    fed = doc["federation"]
    assert fed["created"] is True
    assert fed["name"] == doc["workspace"]["identity_key"]
    assert len(fed["member_repository_ids"]) == 3
    # per-target receipts: ws root + every member
    for m in [ws, ws / "api", ws / "worker", ws / "repo"]:
        rdir = m / ".code-atlas" / "receipts"
        files = list(rdir.glob("install-*.json"))
        assert len(files) == 1, f"missing receipt under {rdir}"
        doc_t = json.loads(files[0].read_text(encoding="utf-8"))
        assert doc_t["schema"] == "forge/InstallReceipt/v1"
        assert doc_t["forge_id"] == "code-atlas-forge"
        assert doc_t["operation"] == "install"
    # consolidated receipt at the ws root is discoverable
    ws_doc = json.loads(
        next((ws / ".code-atlas" / "receipts").glob("*.json")).read_text(encoding="utf-8")
    )
    assert ws_doc["verification"]["status"] == "PASS"
    assert ws_doc["status"] == "completed"
    # no indexing: atlas.db holds repositories, not snapshots
    import sqlite3

    conn = sqlite3.connect(env["data"] / "atlas.db")
    try:
        snaps = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    finally:
        conn.close()
    assert snaps == 0


def test_workspace_install_is_idempotent(env) -> None:
    ws = env["ws"]
    _make_member(ws, "api", "pyproject.toml")
    lifecycle.install(scope="workspace", yes=True)
    doc = lifecycle.install(scope="workspace", yes=True)
    assert doc["status"] == "completed"
    assert doc["verification"]["status"] == "PASS"
    assert doc["members"][0]["status"] == "existing"
    assert doc["federation"]["created"] is False


def test_workspace_dry_run_writes_nothing(env) -> None:
    ws = env["ws"]
    _make_member(ws, "api", "pyproject.toml")
    doc = lifecycle.install(scope="workspace", dry_run=True)
    assert doc["status"] == "planned"
    assert doc["workspace"]["members_discovered"] == 1
    assert any(s.get("member") == "api" for s in doc["plan"])
    assert not _receipt_file(env).exists()
    assert not (ws / ".code-atlas").exists()
    assert not (ws / "api" / ".code-atlas").exists()
    assert not env["data"].exists()


def test_workspace_no_members_is_honest(env) -> None:
    doc = lifecycle.install(scope="workspace", yes=True)
    assert doc["status"] == "completed"
    assert doc["workspace"]["members_discovered"] == 0
    step = next(s for s in doc["skipped"] if s["component"] == "repository")
    assert "no members" in step["result"]
    # federation group still materializes (empty) — honest, not faked
    assert doc["federation"]["workspace_id"]


def test_workspace_member_preserves_user_state(env) -> None:
    ws = env["ws"]
    m = _make_member(ws, "api", "pyproject.toml")
    # user-authored file inside a pre-existing .code-atlas is never clobbered
    (m / ".code-atlas").mkdir()
    (m / ".code-atlas" / ".gitignore").write_text("custom\n", encoding="utf-8")
    (m / ".code-atlas" / "keep.txt").write_text("mine\n", encoding="utf-8")
    lifecycle.install(scope="workspace", yes=True)
    assert (m / ".code-atlas" / ".gitignore").read_text() == "custom\n"
    assert (m / ".code-atlas" / "keep.txt").read_text() == "mine\n"
    doc = lifecycle.uninstall(scope="workspace", yes=True)
    assert doc["status"] == "completed"
    assert (m / ".code-atlas" / ".gitignore").exists()  # not ours — kept
    assert (m / ".code-atlas" / "keep.txt").exists()


# ------------------------------------------------------------ delegation surface


def test_host_and_gateway_args_accepted_and_ignored(env) -> None:
    doc = lifecycle.install(
        yes=True, host="claude", gateway_profile="deep", root=env["ws"]
    )
    assert doc["status"] == "completed"
    assert doc["verification"]["status"] == "PASS"
    assert doc["requested_host"] == "claude"
    assert doc["requested_gateway_profile"] == "deep"
    check = next(c for c in doc["checks"] if c["id"] == "host-gateway-args")
    assert check["status"] == "NOT_APPLICABLE"
    assert doc["host"] is None  # no host payloads — never claimed


def test_cli_delegated_install_emits_receipt_json(env, capsys) -> None:
    rc = cli.main(
        [
            "install",
            "--scope",
            "workspace",
            "--root",
            str(env["ws"]),
            "--host",
            "devin",
            "--gateway-profile",
            "balanced",
            "--yes",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    doc = json.loads(out)  # stdout is a single parseable receipt
    assert doc["schema"] == "forge/InstallReceipt/v1"
    assert doc["operation"] == "install"
    assert doc["status"] == "completed"
    assert doc["verification"]["status"] == "PASS"


def test_cli_delegated_dry_run_emits_plan_receipt(env, capsys) -> None:
    _make_member(env["ws"], "api", "pyproject.toml")
    rc = cli.main(
        [
            "install",
            "--scope",
            "workspace",
            "--root",
            str(env["ws"]),
            "--host",
            "all",
            "--profile",
            "recommended",
            "--dry-run",
        ]
    )
    assert rc == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["schema"] == "forge/InstallReceipt/v1"
    assert doc["status"] == "planned"
    assert not _receipt_file(env).exists()
    assert not (env["ws"] / "api" / ".code-atlas").exists()


def test_cli_unknown_components_exit_1_with_receipt(env, capsys) -> None:
    rc = cli.main(
        [
            "install",
            "--root",
            str(env["ws"]),
            "--host",
            "all",
            "--components",
            "skills,agents",
            "--yes",
        ]
    )
    assert rc == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["schema"] == "forge/InstallReceipt/v1"
    assert doc["status"] == "failed"
    assert doc["verification"]["status"] == "FAIL"
    assert doc["error"]["kind"] == "CA-INSTALL-UNSUPPORTED-COMPONENTS"


def test_cli_json_flag_prints_receipt(env, capsys) -> None:
    rc = cli.main(["install", "--root", str(env["ws"]), "--yes", "--json"])
    assert rc == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["schema"] == "forge/InstallReceipt/v1"
    assert doc["verification"]["status"] == "PASS"


# ------------------------------------------------------------ uninstall


def test_uninstall_workspace_removes_managed_state(env) -> None:
    ws = env["ws"]
    _make_member(ws, "api", "pyproject.toml")
    _make_member(ws, "lib", "pyproject.toml")
    lifecycle.install(scope="workspace", yes=True)
    fed = lifecycle.read_receipt()["federation"]
    doc = lifecycle.uninstall(scope="workspace", yes=True)
    assert doc["status"] == "completed"
    assert not (ws / ".code-atlas").exists()
    assert not (ws / "api" / ".code-atlas").exists()
    assert not (ws / "lib" / ".code-atlas").exists()
    assert not _receipt_file(env).exists()
    # install-created federation group is gone; index state survives
    import sqlite3

    conn = sqlite3.connect(env["data"] / "atlas.db")
    try:
        ws_rows = conn.execute("SELECT COUNT(*) FROM federation_workspaces").fetchone()[0]
        mem_rows = conn.execute("SELECT COUNT(*) FROM federation_members").fetchone()[0]
        repos = conn.execute("SELECT COUNT(*) FROM repositories").fetchone()[0]
    finally:
        conn.close()
    assert fed["created"] is True
    assert ws_rows == 0 and mem_rows == 0
    assert repos == 2  # repositories/index state are not install-owned


def test_status_after_workspace_install(env) -> None:
    _make_member(env["ws"], "api", "pyproject.toml")
    lifecycle.install(scope="workspace", yes=True)
    st = lifecycle.status()
    assert st["installed"] is True
    assert st["receipt"]["schema"] == "forge/InstallReceipt/v1"
    doc = lifecycle.doctor(scope="workspace")
    states = {c["check"]: c["state"] for c in doc["checks"]}
    assert states["receipt"] == "ok"
    assert states["target-receipt"] == "ok"
    assert doc["status"] == "healthy"
