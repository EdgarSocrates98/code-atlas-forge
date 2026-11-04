"""Code Atlas install lifecycle — plan, apply, receipt, doctor.

Honest local-first semantics: install provisions the local environment
(data dir, workspace config, repository registration) and records a
receipt — it never touches hosts, clouds or providers. ``dry_run``
returns the plan and writes nothing; apply requires ``yes``.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from code_atlas import __version__
from code_atlas.config import (
    ConfigError,
    config_file,
    data_dir,
    resolve_root,
    write_config,
)

FORGE_ID = "code-atlas-forge"
SCOPES = ("project", "user")
PROFILES = ("minimal", "recommended", "full")
COMPONENTS = ("config", "data-dir", "repository")
PROFILE_COMPONENTS = {
    "minimal": ("data-dir",),
    "recommended": ("config", "data-dir", "repository"),
    "full": ("config", "data-dir", "repository"),
}
_RECEIPT_SCHEMA = "forge/CodeAtlasInstallReceipt/v1"


def _receipt_path() -> Path:
    return data_dir() / "install" / "receipt.json"


def read_receipt() -> dict[str, Any] | None:
    p = _receipt_path()
    if not p.exists():
        return None
    try:
        doc: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
        return doc
    except json.JSONDecodeError:
        return None


def _profile_components(
    profile: str, components: tuple[str, ...] | None
) -> tuple[str, ...]:
    if components:
        unknown = [c for c in components if c not in COMPONENTS]
        if unknown:
            raise ValueError(
                f"unknown components: {unknown} (valid: {list(COMPONENTS)})"
            )
        return components
    return PROFILE_COMPONENTS.get(profile, PROFILE_COMPONENTS["recommended"])


def _plan(
    *, scope: str, root: Path | None, comps: tuple[str, ...]
) -> list[dict[str, Any]]:
    """Deterministic plan — the same entries apply() will produce.

    ``config``/``repository`` are workspace-scoped: under ``user`` they
    are skipped with a reason instead of pretending to apply."""
    steps: list[dict[str, Any]] = []
    if "data-dir" in comps:
        steps.append({"component": "data-dir", "action": "mkdir",
                      "target": str(data_dir())})
    if "config" in comps:
        if scope == "project":
            steps.append({"component": "config",
                          "action": "write-if-absent",
                          "target": str(config_file())})
        else:
            steps.append({"component": "config", "action": "skipped",
                          "target": "workspace-scoped — needs a project root"})
    if "repository" in comps:
        if scope == "project":
            steps.append({"component": "repository",
                          "action": "register",
                          "target": str(root)})
        else:
            steps.append({"component": "repository", "action": "skipped",
                          "target": "workspace-scoped — needs a project root"})
    steps.append({"component": "receipt", "action": "write",
                  "target": str(_receipt_path())})
    return steps


def install(
    *,
    scope: str = "project",
    root: Path | None = None,
    profile: str = "recommended",
    components: tuple[str, ...] | None = None,
    dry_run: bool = False,
    yes: bool = False,
) -> dict[str, Any]:
    """Plan (dry_run) or apply the install. Returns a receipt doc."""
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r} (valid: {list(SCOPES)})")
    if profile not in PROFILES:
        raise ValueError(
            f"unknown profile {profile!r} (valid: {list(PROFILES)})")
    comps = _profile_components(profile, components)
    resolved = (
        resolve_root(str(root) if root else None)
        if scope == "project"
        else None
    )
    if scope == "project" and resolved is not None and not resolved.is_dir():
        raise ValueError(f"root {resolved} is not a directory")
    steps = _plan(scope=scope, root=resolved, comps=comps)

    doc: dict[str, Any] = {
        "schema": _RECEIPT_SCHEMA,
        "forge_id": FORGE_ID,
        "version": __version__,
        "operation": "install",
        "status": "planned" if dry_run else "completed",
        "scope": scope,
        "profile": profile,
        "components": list(comps),
        "root": str(resolved) if resolved else None,
        "data_dir": str(data_dir()),
        "plan": steps,
        "applied": [],
        "skipped": [],
        "unverified": [],
        "receipt": None,
        "installed_at": None,
    }
    if dry_run:
        return doc
    if not yes:
        return {**doc, "status": "declined",
                "error": {"kind": "CA-INSTALL-DECLINED",
                          "detail": "needs --yes - nothing written"}}

    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    unverified: list[dict[str, Any]] = []
    for s in steps:
        comp, action, target = s["component"], s["action"], s["target"]
        try:
            bucket, result = _apply_step(
                comp, action, target,
                resolved=resolved, scope=scope,
                profile=profile, comps=comps,
            )
        except OSError as exc:
            unverified.append({**s, "result": f"unverified:{exc}"})
            continue
        (applied if bucket == "applied" else skipped).append(
            {**s, "result": result})
        if action == "write" and comp == "receipt" and bucket == "applied":
            doc["receipt"] = result

    doc["applied"] = applied
    doc["skipped"] = skipped
    doc["unverified"] = unverified
    doc["installed_at"] = time.time()
    if unverified:
        doc["status"] = "partial"
    return doc


def _apply_step(
    comp: str, action: str, target: str, *,
    resolved: Path | None, scope: str, profile: str,
    comps: tuple[str, ...],
) -> tuple[str, str]:
    """One plan step -> ("applied"|"skipped", result). May raise OSError."""
    if action == "skipped":
        return "skipped", target
    if action == "mkdir":
        Path(target).mkdir(parents=True, exist_ok=True)
        return "applied", "ok"
    if action == "write-if-absent":
        if resolved is None:
            return "skipped", "user-scope"
        try:
            write_config(resolved)  # raises if config exists
        except ConfigError as exc:
            return "skipped", str(exc)
        return "applied", "written"
    if action == "register":
        if resolved is None:
            return "skipped", "user-scope"
        repo_id = _register_repo(resolved)
        return "applied", f"repository_id={repo_id}"
    if action == "write" and comp == "receipt":
        p = _receipt_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(
            {"schema": _RECEIPT_SCHEMA, "forge_id": FORGE_ID,
             "version": __version__, "scope": scope,
             "profile": profile, "components": list(comps),
             "root": str(resolved) if resolved else None,
             "installed_at": time.time()}, indent=2,
            sort_keys=True), encoding="utf-8")
        return "applied", str(p)
    return "skipped", "no-handler"


def _register_repo(resolved: Path) -> int:
    from code_atlas.discovery.identity import identity_key_for_root
    from code_atlas.storage import connect, migrate
    from code_atlas.storage.store import Store

    conn = connect(data_dir() / "atlas.db")
    try:
        migrate(conn)
        repo_id = Store(conn).get_or_create_repository(
            identity_key_for_root(resolved), str(resolved)
        )
        conn.commit()
        return repo_id
    finally:
        conn.close()


def status() -> dict[str, Any]:
    r = read_receipt()
    return {"forge_id": FORGE_ID, "installed": r is not None, "receipt": r}


def doctor(*, scope: str = "project", root: Path | None = None) -> dict[str, Any]:
    """Health check — receipt readable, data dir writable, db openable."""
    checks: list[dict[str, Any]] = []
    r = read_receipt()
    checks.append({"check": "receipt", "state": "ok" if r else "missing",
                   "target": str(_receipt_path())})
    dd = data_dir()
    try:
        dd.mkdir(parents=True, exist_ok=True)
        probe = dd / ".atlas-probe"
        probe.write_text("ok")
        probe.unlink()
        state = "ok"
    except OSError:
        state = "unwritable"
    checks.append({"check": "data-dir", "state": state, "target": str(dd)})
    cfg = config_file()
    if scope == "project":
        checks.append({"check": "config",
                       "state": "ok" if cfg.exists() else "missing",
                       "target": str(cfg)})
    db = dd / "atlas.db"
    try:
        conn = sqlite3.connect(db)
        conn.execute("SELECT 1")
        conn.close()
        db_state = "ok"
    except sqlite3.Error:
        db_state = "error"
    checks.append({"check": "database", "state": db_state, "target": str(db)})
    checks.append({"check": "python>=3.12",
                   "state": "ok" if sys.version_info >= (3, 12) else "fail",
                   "target": sys.version.split()[0]})
    bad = {"unwritable", "error", "fail"}
    ok = r is not None and all(c["state"] not in bad for c in checks)
    return {"forge_id": FORGE_ID, "scope": scope, "checks": checks,
            "status": "healthy" if ok else "degraded"}


__all__ = [
    "COMPONENTS",
    "FORGE_ID",
    "PROFILES",
    "PROFILE_COMPONENTS",
    "SCOPES",
    "doctor",
    "install",
    "read_receipt",
    "status",
]
