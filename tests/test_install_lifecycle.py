"""Install lifecycle — plan/apply/receipt/doctor honest semantics.

Isolation is env-based: CODE_ATLAS_DATA_DIR / CODE_ATLAS_CONFIG_DIR /
CODE_ATLAS_ROOT point at tmp dirs, so nothing touches the real profile.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_atlas.install import lifecycle


@pytest.fixture()
def env(monkeypatch, tmp_path):
    data = tmp_path / "data"
    conf = tmp_path / "conf"
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setenv("CODE_ATLAS_DATA_DIR", str(data))
    monkeypatch.setenv("CODE_ATLAS_CONFIG_DIR", str(conf))
    monkeypatch.setenv("CODE_ATLAS_ROOT", str(root))
    return {"data": data, "conf": conf, "root": root}


def _receipt_file(env) -> Path:
    return env["data"] / "install" / "receipt.json"


# ---------------------------------------------------------------- plan


def test_dry_run_plans_and_writes_nothing(env) -> None:
    doc = lifecycle.install(dry_run=True)
    assert doc["status"] == "planned"
    assert doc["schema"] == "forge/CodeAtlasInstallReceipt/v1"
    kinds = {(s["component"], s["action"]) for s in doc["plan"]}
    assert ("data-dir", "mkdir") in kinds
    assert ("config", "write-if-absent") in kinds
    assert ("repository", "register") in kinds
    assert ("receipt", "write") in kinds
    assert not _receipt_file(env).exists()
    assert not env["data"].exists()


def test_apply_without_yes_is_declined_and_writes_nothing(env) -> None:
    doc = lifecycle.install()
    assert doc["status"] == "declined"
    assert doc["error"]["kind"] == "CA-INSTALL-DECLINED"
    assert not _receipt_file(env).exists()


# ---------------------------------------------------------------- apply


def test_apply_project_scope(env) -> None:
    doc = lifecycle.install(yes=True)
    assert doc["status"] == "completed"
    assert env["data"].is_dir()
    assert (env["conf"] / "config.toml").is_file()
    receipt = json.loads(_receipt_file(env).read_text(encoding="utf-8"))
    assert receipt["forge_id"] == "code-atlas-forge"
    assert receipt["scope"] == "project"
    applied = {s["component"] for s in doc["applied"]}
    assert {"data-dir", "config", "repository", "receipt"} <= applied
    reg = next(s for s in doc["applied"] if s["component"] == "repository")
    assert reg["result"].startswith("repository_id=")
    assert (env["data"] / "atlas.db").is_file()


def test_reinstall_skips_existing_config(env) -> None:
    lifecycle.install(yes=True)
    doc = lifecycle.install(yes=True)
    cfg_step = next(s for s in doc["skipped"] + doc["applied"]
                    if s["component"] == "config")
    assert "already exists" in cfg_step["result"] or cfg_step["action"] == "skipped"


def test_user_scope_skips_workspace_components(env) -> None:
    doc = lifecycle.install(scope="user", yes=True)
    by_comp = {s["component"]: s for s in doc["plan"]}
    assert by_comp["config"]["action"] == "skipped"
    assert by_comp["repository"]["action"] == "skipped"
    assert env["data"].is_dir()
    assert _receipt_file(env).is_file()
    assert not (env["conf"] / "config.toml").exists()


def test_minimal_profile_only_data_dir(env) -> None:
    doc = lifecycle.install(profile="minimal", yes=True)
    assert doc["components"] == ["data-dir"]
    assert env["data"].is_dir()
    assert not (env["conf"] / "config.toml").exists()


# ------------------------------------------------------------ validation


def test_unknown_scope_and_profile_rejected(env) -> None:
    with pytest.raises(ValueError):
        lifecycle.install(scope="bogus", dry_run=True)
    with pytest.raises(ValueError):
        lifecycle.install(profile="bogus", dry_run=True)


def test_unknown_component_rejected(env) -> None:
    with pytest.raises(ValueError) as exc:
        lifecycle.install(components=("bogus",), dry_run=True)
    assert "bogus" in str(exc.value)


def test_missing_root_rejected(env) -> None:
    with pytest.raises(ValueError):
        lifecycle.install(root=env["root"] / "nope", dry_run=True)


# ------------------------------------------------------------ status/doctor


def test_status_uninstalled(env) -> None:
    st = lifecycle.status()
    assert st["installed"] is False
    assert st["receipt_state"] == "missing"
    assert st["receipt"] is None


def test_status_and_doctor_after_install(env) -> None:
    lifecycle.install(yes=True)
    st = lifecycle.status()
    assert st["installed"] is True
    assert st["receipt_state"] == "ok"
    doc = lifecycle.doctor(scope="project")
    assert doc["status"] == "healthy"
    states = {c["check"]: c["state"] for c in doc["checks"]}
    assert states["receipt"] == "ok"
    assert states["data-dir"] == "ok"
    assert states["database"] == "ok"
    assert states["config"] == "ok"


def test_corrupt_receipt_is_error_not_missing(env) -> None:
    p = _receipt_file(env)
    p.parent.mkdir(parents=True)
    p.write_text("{ not json", encoding="utf-8")
    st = lifecycle.status()
    assert st["installed"] is False
    assert st["receipt_state"] == "error"
    doc = lifecycle.doctor()
    assert doc["status"] == "degraded"
    receipt_check = next(c for c in doc["checks"] if c["check"] == "receipt")
    assert receipt_check["state"] == "error"


def test_doctor_missing_receipt_is_degraded(env) -> None:
    doc = lifecycle.doctor()
    assert doc["status"] == "degraded"
    receipt_check = next(c for c in doc["checks"] if c["check"] == "receipt")
    assert receipt_check["state"] == "missing"


# ------------------------------------------------------------ uninstall


def test_uninstall_dry_run_and_declined(env) -> None:
    lifecycle.install(yes=True)
    doc = lifecycle.uninstall(dry_run=True)
    assert doc["status"] == "planned"
    assert _receipt_file(env).exists()  # nothing removed
    doc = lifecycle.uninstall()
    assert doc["status"] == "declined"
    assert doc["error"]["kind"] == "CA-UNINSTALL-DECLINED"
    assert _receipt_file(env).exists()


def test_uninstall_removes_install_owned_artifacts(env) -> None:
    lifecycle.install(yes=True)
    doc = lifecycle.uninstall(yes=True)
    assert doc["status"] == "completed"
    assert not _receipt_file(env).exists()
    assert not (env["conf"] / "config.toml").exists()
    # index state survives without --purge — uninstall ≠ data deletion
    assert (env["data"] / "atlas.db").exists()
    st = lifecycle.status()
    assert st["installed"] is False


def test_uninstall_purge_removes_index_state(env) -> None:
    lifecycle.install(yes=True)
    assert (env["data"] / "atlas.db").exists()
    doc = lifecycle.uninstall(yes=True, purge_state=True)
    assert doc["status"] == "completed"
    assert not (env["data"] / "atlas.db").exists()
    assert not _receipt_file(env).exists()


def test_uninstall_keeps_config_not_proven_install_owned(env) -> None:
    # config written by hand (or receipt missing) — uninstall must not
    # delete what it cannot prove the install created.
    (env["conf"]).mkdir(parents=True)
    (env["conf"] / "config.toml").write_text("[x]\ny = 1\n", encoding="utf-8")
    doc = lifecycle.uninstall(yes=True)
    assert (env["conf"] / "config.toml").exists()
    cfg = next(s for s in doc["skipped"] if s["component"] == "config")
    assert "kept" in cfg["result"]


def test_uninstall_without_receipt_still_clears_receipt_step(env) -> None:
    doc = lifecycle.uninstall(yes=True)
    assert doc["status"] == "completed"
    receipt_step = next(s for s in doc["plan"] if s["component"] == "receipt")
    assert receipt_step["action"] == "remove"
