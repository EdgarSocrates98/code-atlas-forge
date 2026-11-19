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
    # install-2.1: canonical delegation envelope, atlas extras on top
    assert doc["schema"] == "forge/InstallReceipt/v1"
    assert doc["forge_id"] == "code-atlas-forge"
    assert doc["operation"] == "install"
    assert doc["verification"]["status"] == "UNVERIFIED"
    # cycle 16.1 wave-09: derived forge/InstallLifecycle/v1 block
    assert doc["lifecycle"] == {
        "schema": "forge/InstallLifecycle/v1",
        "state": "planned",
        "detail": "plan emitted — nothing written",
    }
    kinds = {(s["component"], s["action"]) for s in doc["plan"]}
    assert ("data-dir", "mkdir") in kinds
    assert ("config", "write-if-absent") in kinds
    assert ("repository", "register") in kinds
    assert ("receipt", "write") in kinds
    assert not _receipt_file(env).exists()
    assert not env["data"].exists()


def test_apply_without_yes_is_blocked_and_writes_nothing(env) -> None:
    doc = lifecycle.install()
    # v1 vocabulary: the approval-gate refusal is `blocked` (was
    # `declined` pre-16.1); the typed refusal kind is unchanged.
    assert doc["status"] == "blocked"
    assert doc["lifecycle"]["state"] == "blocked"
    assert doc["error"]["kind"] == "CA-INSTALL-DECLINED"
    assert not _receipt_file(env).exists()


# ---------------------------------------------------------------- apply


def test_apply_project_scope(env) -> None:
    doc = lifecycle.install(yes=True)
    assert doc["status"] == "completed"
    assert doc["verification"]["status"] == "PASS"
    assert env["data"].is_dir()
    assert (env["conf"] / "config.toml").is_file()
    receipt = json.loads(_receipt_file(env).read_text(encoding="utf-8"))
    assert receipt["schema"] == "forge/InstallReceipt/v1"
    assert receipt["forge_id"] == "code-atlas-forge"
    assert receipt["operation"] == "install"
    assert receipt["verification"]["status"] == "PASS"
    assert receipt["scope"] == "project"
    applied = {s["component"] for s in doc["applied"]}
    assert {"data-dir", "config", "repository", "receipt"} <= applied
    reg = next(s for s in doc["applied"] if s["component"] == "repository")
    assert reg["result"].startswith("repository_id=")
    assert (env["data"] / "atlas.db").is_file()
    # per-target receipt under <root>/.code-atlas/receipts (delegation dir)
    receipts = list((env["root"] / ".code-atlas" / "receipts").glob("*.json"))
    assert len(receipts) == 1
    target = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert target["schema"] == "forge/InstallReceipt/v1"
    assert target["target_root"] == str(env["root"])
    assert target["lifecycle"]["schema"] == "forge/InstallLifecycle/v1"
    assert target["lifecycle"]["state"] == "verified"
    assert (env["root"] / ".code-atlas" / ".gitignore").read_text() == "*\n"
    assert doc["lifecycle"]["state"] == "verified"


def test_reinstall_skips_existing_config(env) -> None:
    lifecycle.install(yes=True)
    doc = lifecycle.install(yes=True)
    cfg_step = next(s for s in doc["skipped"] if s["component"] == "config")
    assert "unchanged" in cfg_step["result"]


def test_incremental_reinstall_never_rewrites_unchanged_assets(env) -> None:
    """§7.4: a repeated install must not rewrite unchanged assets —
    durable files keep bytes+mtime, ownership is carried forward as
    ``unchanged``, and the only new writes are the new-cycle receipts
    (new filenames — never rewrites)."""
    lifecycle.install(yes=True)
    cfg = env["conf"] / "config.toml"
    gitignore = env["root"] / ".code-atlas" / ".gitignore"
    rdir = env["root"] / ".code-atlas" / "receipts"
    cfg_mtime, gi_mtime = cfg.stat().st_mtime_ns, gitignore.stat().st_mtime_ns
    receipts_before = {p.name for p in rdir.glob("*.json")}

    doc = lifecycle.install(yes=True)

    # durable assets untouched — no rewrite of unchanged content
    assert cfg.stat().st_mtime_ns == cfg_mtime
    assert gitignore.stat().st_mtime_ns == gi_mtime
    assert gitignore.read_bytes() == b"*\n"
    # ...but still managed: carried forward as `unchanged`, never adopted
    managed = {(m["path"], m["action"]) for m in doc["managed_files"]}
    assert (".code-atlas/.gitignore", "unchanged") in managed
    cfg_entry = next(
        m for m in doc["managed_files"] if m["action"] == "unchanged" and "config" in m["path"]
    )
    assert cfg_entry["sha256"]
    # durable steps skipped, repository re-registers as existing
    assert not any(s["component"] == "config" for s in doc["applied"])
    reg = next(s for s in doc["skipped"] if s["component"] == "repository")
    assert "existing" in reg["result"]
    # new receipts are new files; prior evidence is preserved
    receipts_after = {p.name for p in rdir.glob("*.json")}
    assert receipts_before < receipts_after
    assert len(receipts_after - receipts_before) == 1
    assert doc["lifecycle"]["state"] == "verified"


def test_reinstall_then_uninstall_still_removes_install_owned_config(env) -> None:
    """Regression — an incremental reinstall must not lose ownership:
    the second receipt keeps the managed entry (``unchanged``), so
    uninstall still removes what install created."""
    lifecycle.install(yes=True)
    lifecycle.install(yes=True)
    doc = lifecycle.uninstall(yes=True)
    assert doc["status"] == "completed"
    assert not (env["conf"] / "config.toml").exists()
    assert not (env["root"] / ".code-atlas").exists()


def test_reinstall_preserves_modified_managed_config(env) -> None:
    """A user edit makes the config drift — reinstall preserves it and
    does NOT carry it forward as managed (it is no longer ours)."""
    lifecycle.install(yes=True)
    cfg = env["conf"] / "config.toml"
    cfg.write_text("# user edits\n[workspace]\nroot = '/x'\n", encoding="utf-8")
    doc = lifecycle.install(yes=True)
    assert "user edits" in cfg.read_text()
    cfg_step = next(s for s in doc["skipped"] if s["component"] == "config")
    assert "not proven" in cfg_step["result"]
    assert all("config" not in m["path"] for m in doc["managed_files"])


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
    assert doc["components"] == {"selected": ["data-dir"]}
    assert env["data"].is_dir()
    assert not (env["conf"] / "config.toml").exists()


# ------------------------------------------------------------ validation


def test_unknown_scope_and_profile_rejected(env) -> None:
    with pytest.raises(ValueError):
        lifecycle.install(scope="bogus", dry_run=True)
    with pytest.raises(ValueError):
        lifecycle.install(profile="bogus", dry_run=True)


def test_unknown_component_is_typed_refusal(env) -> None:
    # Orchestrator-style component names get a typed refusal receipt —
    # a valid forge/InstallReceipt/v1 with FAIL, never a ValueError/exit-2.
    doc = lifecycle.install(components=("bogus", "skills"), dry_run=True)
    assert doc["schema"] == "forge/InstallReceipt/v1"
    assert doc["status"] == "failed"
    assert doc["verification"]["status"] == "FAIL"
    assert doc["lifecycle"]["state"] == "failed"
    assert doc["error"]["kind"] == "CA-INSTALL-UNSUPPORTED-COMPONENTS"
    assert doc["error"]["rejected"] == ["bogus", "skills"]
    assert "bogus" in doc["error"]["detail"]
    assert not _receipt_file(env).exists()  # refusal writes nothing
    doc = lifecycle.install(components=("agents",), yes=True)
    assert doc["status"] == "failed"
    assert not _receipt_file(env).exists()


def test_missing_root_rejected(env) -> None:
    with pytest.raises(ValueError):
        lifecycle.install(root=env["root"] / "nope", dry_run=True)


# ------------------------------------------------------------ status/doctor


def test_status_uninstalled(env) -> None:
    st = lifecycle.status()
    # forge/InstallationHealth/v1: no evidence → unverified, never a guess
    assert st["schema"] == "forge/InstallationHealth/v1"
    assert st["installed"] is False
    assert st["receipt_state"] == "missing"
    assert st["status"] == "unverified"
    assert st["lifecycle"]["state"] == "unverified"
    assert st["receipt"] is None


def test_status_and_doctor_after_install(env) -> None:
    lifecycle.install(yes=True)
    st = lifecycle.status()
    assert st["schema"] == "forge/InstallationHealth/v1"
    assert st["installed"] is True
    assert st["receipt_state"] == "ok"
    assert st["status"] == "healthy"
    assert st["lifecycle"] == {
        "schema": "forge/InstallLifecycle/v1",
        "state": "ready",
        "detail": "managed evidence verified on disk",
    }
    assert st["drift"]["ok"] == 2  # config + .gitignore still on disk
    assert st["context"]["managed_entries"] == 2
    assert st["context"]["tools_exposed"] is not None
    doc = lifecycle.doctor(scope="project")
    assert doc["schema"] == "forge/InstallationHealth/v1"
    assert doc["status"] == "healthy"
    assert doc["lifecycle"]["state"] == "ready"
    states = {c["id"]: c["status"] for c in doc["checks"]}
    assert states["receipt"] == "PASS"
    assert states["data-dir"] == "PASS"
    assert states["database"] == "PASS"
    assert states["config"] == "PASS"
    assert all("repairable" in c for c in doc["checks"])


def test_status_detects_managed_drift(env) -> None:
    lifecycle.install(yes=True)
    (env["conf"] / "config.toml").write_text("# edited\n", encoding="utf-8")
    st = lifecycle.status()
    assert st["status"] == "degraded"
    assert st["lifecycle"]["state"] == "partially_applied"
    assert st["drift"]["modified"]


def test_corrupt_receipt_is_error_not_missing(env) -> None:
    p = _receipt_file(env)
    p.parent.mkdir(parents=True)
    p.write_text("{ not json", encoding="utf-8")
    st = lifecycle.status()
    assert st["installed"] is False
    assert st["receipt_state"] == "error"
    # a corrupt receipt is evidence that cannot parse — broken, not absent
    assert st["status"] == "broken"
    doc = lifecycle.doctor()
    assert doc["status"] == "broken"
    assert doc["lifecycle"]["state"] == "failed"
    receipt_check = next(c for c in doc["checks"] if c["id"] == "receipt")
    assert receipt_check["status"] == "FAIL"


def test_doctor_missing_receipt_is_unverified(env) -> None:
    # v1 vocabulary: no install evidence → `unverified`, not `degraded`
    doc = lifecycle.doctor()
    assert doc["status"] == "unverified"
    assert doc["lifecycle"]["state"] == "unverified"
    receipt_check = next(c for c in doc["checks"] if c["id"] == "receipt")
    assert receipt_check["status"] == "UNVERIFIED"


# ------------------------------------------------------------ uninstall


def test_uninstall_dry_run_and_blocked(env) -> None:
    lifecycle.install(yes=True)
    doc = lifecycle.uninstall(dry_run=True)
    assert doc["status"] == "planned"
    assert doc["lifecycle"]["state"] == "planned"
    assert _receipt_file(env).exists()  # nothing removed
    doc = lifecycle.uninstall()
    assert doc["status"] == "blocked"
    assert doc["lifecycle"]["state"] == "blocked"
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


def test_uninstall_keeps_user_modified_config(env) -> None:
    """Drift-aware removal: the receipt proves install wrote the config,
    but a user edit since then makes it foreign content — kept."""
    lifecycle.install(yes=True)
    cfg = env["conf"] / "config.toml"
    cfg.write_text("# user edits after install\n", encoding="utf-8")
    doc = lifecycle.uninstall(yes=True)
    assert doc["status"] == "completed"
    assert cfg.exists()
    kept = next(s for s in doc["skipped"] if s["component"] == "config")
    assert "modified" in kept["result"]


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
