"""Code Atlas install lifecycle — plan, apply, receipt, doctor.

Honest local-first semantics: install provisions the local environment
(data dir, workspace config, repository registration) and records a
receipt — it never touches hosts, clouds or providers. ``dry_run``
returns the plan and writes nothing; apply requires ``yes``.

Receipt contract (install-2.1): ``forge/InstallReceipt/v1`` — the
envelope the-forge's delegation verifier reads (``forge_id`` +
``operation="install"`` + ``status`` + ``verification.status``).
Every receipt also carries a ``lifecycle`` block —
``forge/InstallLifecycle/v1``: the finer-grained position of the
installation *derived* from ``status`` + ``verification``, never
asserted by the writer (the same derivation the-forge applies —
``planned`` → ``applied`` → ``verified``, with ``blocked``, ``failed``
and ``unverified`` for gate refusals, errors and evidence gaps).
``status`` uses the v1 receipt vocabulary: an approval-gate refusal is
``blocked`` (was ``declined`` pre-16.1), a plan is ``planned``, a
finished operation ``completed`` — which never implies readiness.

Health contract (cycle 16.1): ``status``/``doctor`` emit
``forge/InstallationHealth/v1``-aligned docs — closed ``status``
vocabulary (``healthy|degraded|broken|unverified``), checks as
``{id, status, detail, repairable}``, a ``context`` block with
observable metrics only, and the same derived ``lifecycle`` block
(``healthy → ready``, ``degraded → partially_applied``,
``broken → failed``, ``unverified → unverified``).

``installation_kind=service``: the atlas is a local service — no host
payloads, no execution profiles. ``host``/``gateway_profile`` are
accepted for the delegation surface and honestly recorded as ignored.

Scopes:

- ``project`` — one repository root (default; ``--root`` > env > cwd);
- ``workspace`` — a multi-repo root whose members are discovered by
  markers (``.git`` or a project manifest — gitless members count).
  Every member is registered under its own stable ``identity_key`` and
  grouped into a federation workspace named by the root's identity;
- ``user`` — the global data dir only.

Install **never indexes**: registering a member only enables explicit
per-member ``code-atlas index`` runs and the federation layer — no
member source is scanned here. ``full`` is declared equal to
``recommended``: there are no extra installable components.
"""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import sqlite3
import sys
import time
from datetime import UTC, datetime
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
from code_atlas.install.workspace import (
    RECEIPTS_DIRNAME,
    discover_projects,
    receipts_dir,
    state_dir,
)

FORGE_ID = "code-atlas-forge"
SCOPES = ("project", "workspace", "user")
PROFILES = ("minimal", "recommended", "full")
COMPONENTS = ("config", "data-dir", "repository")
PROFILE_COMPONENTS = {
    "minimal": ("data-dir",),
    "recommended": ("config", "data-dir", "repository"),
    "full": ("config", "data-dir", "repository"),
}
SCHEMA_RECEIPT = "forge/InstallReceipt/v1"
SCHEMA_HEALTH = "forge/InstallationHealth/v1"
SCHEMA_LIFECYCLE = "forge/InstallLifecycle/v1"

E_UNSUPPORTED_COMPONENTS = "CA-INSTALL-UNSUPPORTED-COMPONENTS"
E_INSTALL_DECLINED = "CA-INSTALL-DECLINED"
E_UNINSTALL_DECLINED = "CA-UNINSTALL-DECLINED"

# Receipt ``status`` values that mean a gate refused the operation —
# the-forge's ``_BLOCKED_STATUSES`` family (``blocked|refused|unsupported``)
# plus ``declined``, kept so pre-16.1 receipts still derive correctly.
_BLOCKED_STATUSES = frozenset({"blocked", "refused", "unsupported", "declined"})

# Content the install-owned ``<target>/.code-atlas/.gitignore`` carries —
# only a file with exactly this payload is removed on uninstall.
_GITIGNORE_PAYLOAD = "*\n"

# federation_workspaces.name limit (Federation.MAX_WORKSPACE_NAME) —
# duplicated here so discovery stays import-free at module load.
_WS_NAME_LIMIT = 120


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _receipt_path() -> Path:
    """Global receipt — the "latest install" doc ``status``/``uninstall``
    use as ownership proof. Per-target receipts live under
    ``<target>/.code-atlas/receipts/`` (delegation convention)."""
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


def _receipt_lifecycle(doc: dict[str, Any]) -> dict[str, Any]:
    """Derive the ``forge/InstallLifecycle/v1`` block of a receipt.

    The state is a *view* over ``status`` + ``verification`` — the same
    closed derivation the-forge applies in ``install/lifecycle.py`` so a
    reader recomputing it reaches the same answer. ``planned`` →
    ``applied`` → ``verified`` is the success line; gate refusals are
    ``blocked``, errors ``failed``, evidence gaps ``unverified``. The
    atlas never emits batch ``forges[]`` rows, so ``partially_applied``
    is not derivable here — member-level gaps surface as
    ``verification=UNVERIFIED`` → ``applied`` instead of a false PASS.
    """
    status = str(doc.get("status") or "")
    verification = str((doc.get("verification") or {}).get("status") or "")
    if status == "planned":
        state, detail = "planned", "plan emitted — nothing written"
    elif status in _BLOCKED_STATUSES:
        state, detail = "blocked", f"gate refusal: {status}"
    elif status == "completed":
        if verification == "PASS":
            state, detail = "verified", "writes executed and receipt checks pass"
        elif verification == "FAIL":
            state, detail = "failed", "writes executed but verification failed"
        else:
            state, detail = (
                "applied",
                f"writes executed; verification {verification or 'absent'}",
            )
    elif status == "failed":
        state, detail = "failed", "operation errored"
    else:
        state, detail = (
            "unverified",
            f"status {status!r} outside the known receipt vocabulary",
        )
    return {"schema": SCHEMA_LIFECYCLE, "state": state, "detail": detail}


def _health_lifecycle(health_status: str) -> dict[str, Any]:
    """``forge/InstallLifecycle/v1`` for an InstallationHealth doc —
    the-forge's ``health_state`` table: ``healthy → ready``,
    ``degraded → partially_applied``, ``broken → failed``,
    ``unverified → unverified``."""
    table = {
        "healthy": ("ready", "managed evidence verified on disk"),
        "degraded": (
            "partially_applied",
            "drift or failing checks on part of the managed set — repairable",
        ),
        "broken": ("failed", "managed state unrecoverable by repair"),
        "unverified": ("unverified", "no installation evidence at this scope"),
    }
    state, detail = table.get(
        health_status, ("unverified", f"health status {health_status!r} unknown")
    )
    return {"schema": SCHEMA_LIFECYCLE, "state": state, "detail": detail}


def _profile_components(profile: str, components: tuple[str, ...] | None) -> tuple[str, ...]:
    if components:
        return components
    return PROFILE_COMPONENTS.get(profile, PROFILE_COMPONENTS["recommended"])


def _rel(target_root: Path | None, path: Path) -> str:
    """Receipt path relative to the install target root when possible."""
    if target_root is not None:
        try:
            return path.relative_to(target_root).as_posix()
        except ValueError:
            pass
    return str(path)


def _resolve_managed(receipt: dict[str, Any], raw: Any) -> Path | None:
    """Absolute path of a ``managed_files`` entry — relative entries hang
    off the receipt's ``target_root``; absolute ones resolve as-is."""
    if not isinstance(raw, str) or not raw:
        return None
    p = Path(raw)
    if p.is_absolute():
        return p.resolve()
    troot = receipt.get("target_root")
    if not isinstance(troot, str):
        return None
    return (Path(troot) / p).resolve()


def _prior_managed_map(receipt: dict[str, Any] | None) -> dict[str, str]:
    """``{resolved managed path: sha256}`` from the *previous* global
    receipt — the ownership evidence an incremental reinstall uses to
    carry forward still-managed assets as ``unchanged`` (§7.4)."""
    out: dict[str, str] = {}
    if not isinstance(receipt, dict):
        return out
    for entry in receipt.get("managed_files") or []:
        if not isinstance(entry, dict):
            continue
        p = _resolve_managed(receipt, entry.get("path"))
        sha = entry.get("sha256")
        if p is not None and isinstance(sha, str):
            out[str(p)] = sha
    return out


def _prior_unchanged(ctx: _ApplyCtx, path: Path) -> bool:
    """True when the previous receipt lists ``path`` as managed AND its
    on-disk sha still matches — the asset is ours and untouched, so the
    reinstall records it ``unchanged`` instead of dropping ownership."""
    prior_sha = ctx.prior_managed.get(str(path.resolve()))
    if prior_sha is None:
        return False
    try:
        return _sha_file(path) == prior_sha
    except OSError:
        return False


def _managed_drift(receipt: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Drift over the receipt's ``managed_files`` — the atlas ledger.

    ``ok`` = on disk with the recorded sha; ``modified`` = content or
    readability diverged; ``missing`` = not on disk.
    """
    out: list[dict[str, Any]] = []
    if not isinstance(receipt, dict):
        return out
    for entry in receipt.get("managed_files") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            continue
        p = _resolve_managed(receipt, entry["path"])
        if p is None or not p.is_file():
            out.append({"path": entry["path"], "status": "missing"})
            continue
        try:
            same = _sha_file(p) == entry.get("sha256")
        except OSError:
            same = False
        out.append({"path": entry["path"], "status": "ok" if same else "modified"})
    return out


def _managed_sha_for(receipt: dict[str, Any] | None, path: Path) -> str | None:
    """The sha256 the receipt records for ``path`` — ``""`` when a
    managed entry proves ownership but carries no hash, ``None`` when no
    entry resolves to the path."""
    if not isinstance(receipt, dict):
        return None
    target = str(path.resolve())
    for entry in receipt.get("managed_files") or []:
        if not isinstance(entry, dict):
            continue
        p = _resolve_managed(receipt, entry.get("path"))
        if p is not None and str(p) == target:
            return entry["sha256"] if isinstance(entry.get("sha256"), str) else ""
    return None


def _tool_count() -> int | None:
    """Tools the MCP server would enumerate — a static, observable count
    (never inferred; ``None`` when the tool table cannot be read)."""
    try:
        from code_atlas.mcp import _TOOLS
    except Exception:
        return None
    return len(_TOOLS)


def _receipt_filename(created_at: str) -> str:
    return f"install-{created_at.replace(':', '').replace('-', '')}.json"


def _receipt_targets(
    scope: str, resolved: Path | None, members: list[Path], comps: tuple[str, ...]
) -> list[Path]:
    """Targets that get ``.<state>/receipts`` evidence — the workspace
    root plus every member under ``workspace`` scope, just the root
    under ``project`` scope, nothing under ``user``.

    Member receipts exist only when ``repository`` is installed: a
    member that was never registered is not part of this install, and
    writing ``.code-atlas/`` evidence into it would falsely claim a
    failed member operation (§F1 — discovered ≠ attempted)."""
    if scope == "workspace" and resolved is not None:
        seen: set[Path] = set()
        out: list[Path] = []
        targets = [resolved, *members] if "repository" in comps else [resolved]
        for t in targets:
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out
    if scope == "project" and resolved is not None:
        return [resolved]
    return []


def _plan(
    *,
    scope: str,
    root: Path | None,
    comps: tuple[str, ...],
    members: list[Path],
    ws_name: str | None,
    receipt_name: str,
) -> list[dict[str, Any]]:
    """Deterministic plan — the same entries apply() will produce.

    ``config``/``repository`` are project/workspace-scoped: under
    ``user`` they are skipped with a reason instead of pretending to
    apply. Under ``workspace`` the ``repository`` component expands to
    one registration per discovered member plus the federation group —
    members without markers were never discovered, never registered."""
    steps: list[dict[str, Any]] = []
    if "data-dir" in comps:
        steps.append({"component": "data-dir", "action": "mkdir", "target": str(data_dir())})
    if "config" in comps:
        if scope in ("project", "workspace"):
            steps.append(
                {"component": "config", "action": "write-if-absent", "target": str(config_file())}
            )
        else:
            steps.append(
                {
                    "component": "config",
                    "action": "skipped",
                    "target": "workspace-scoped — needs a project root",
                }
            )
    if "repository" in comps:
        if scope == "project":
            steps.append({"component": "repository", "action": "register", "target": str(root)})
        elif scope == "workspace":
            steps.append(
                {
                    "component": "repository",
                    "action": "ensure-workspace",
                    "target": ws_name or "?",
                }
            )
            if members:
                steps.extend(
                    {
                        "component": "repository",
                        "action": "register",
                        "member": m.name,
                        "target": str(m),
                    }
                    for m in members
                )
            else:
                steps.append(
                    {
                        "component": "repository",
                        "action": "skipped",
                        "target": "no members discovered under root",
                    }
                )
        else:
            steps.append(
                {
                    "component": "repository",
                    "action": "skipped",
                    "target": "workspace-scoped — needs a project root",
                }
            )
    for target in _receipt_targets(scope, root, members, comps):
        steps.append(
            {
                "component": "receipt",
                "action": "write-if-absent",
                "target": str(state_dir(target) / ".gitignore"),
                "receipt_target": str(target),
            }
        )
        steps.append(
            {
                "component": "receipt",
                "action": "write",
                "target": str(receipts_dir(target) / receipt_name),
                "receipt_target": str(target),
            }
        )
    steps.append({"component": "receipt", "action": "write", "target": str(_receipt_path())})
    return steps


def _base_doc(
    *,
    scope: str,
    profile: str,
    comps: tuple[str, ...],
    resolved: Path | None,
    dry_run: bool,
    host: str | None,
    gateway_profile: str | None,
    created_at: str,
) -> dict[str, Any]:
    """The canonical ``forge/InstallReceipt/v1`` envelope + atlas extras."""
    doc: dict[str, Any] = {
        "schema": SCHEMA_RECEIPT,
        "forge_id": FORGE_ID,
        "operation": "install",
        "scope": scope,
        "profile": profile,
        "components": {"selected": list(comps)},
        "target_root": str(resolved) if resolved else None,
        # a service forge installs no host payloads — recorded, never claimed
        "host": None,
        "dry_run": dry_run,
        "managed_files": [],
        "managed_markers": [],
        "mcp": [],
        "env": {"python": platform.python_version()},
        "checks": [],
        "verification": {"status": "UNVERIFIED"},
        "status": "planned" if dry_run else "completed",
        "created_at": created_at,
        "created_by": f"code-atlas-forge/{__version__}",
        # -- atlas extras -------------------------------------------------
        "version": __version__,
        "root": str(resolved) if resolved else None,
        "data_dir": str(data_dir()),
        "plan": [],
        "applied": [],
        "skipped": [],
        "unverified": [],
        "members": [],
        "workspace": None,
        "federation": None,
        "receipt": None,
        "receipt_files": [],
        "installed_at": None,
        "requested_host": host,
        "requested_gateway_profile": gateway_profile,
        "notes": [
            "installation_kind=service — no host payloads; state + "
            "repository registration only",
            "install never indexes — run `code-atlas index --root <member>` "
            "per member explicitly",
        ],
    }
    if profile == "full":
        doc["notes"].append(
            "profile `full` is declared equal to `recommended` — code-atlas "
            "ships no extra installable components; indexing is never part "
            "of an install profile"
        )
    if host is not None or gateway_profile is not None:
        doc["checks"].append(
            {
                "id": "host-gateway-args",
                "status": "NOT_APPLICABLE",
                "detail": "service forge — --host/--gateway-profile accepted "
                "and ignored (no host payloads, no execution profile)",
            }
        )
    return doc


def _refusal_doc(
    *,
    kind: str,
    detail: str,
    rejected: list[str],
    scope: str,
    profile: str,
    components: tuple[str, ...] | None,
    resolved: Path | None,
    dry_run: bool,
    host: str | None,
    gateway_profile: str | None,
    created_at: str,
) -> dict[str, Any]:
    """A typed refusal is still a canonical receipt — status ``failed``
    + verification ``FAIL``; the request could not be honored as asked."""
    doc = _base_doc(
        scope=scope,
        profile=profile,
        comps=tuple(components) if components else (),
        resolved=resolved,
        dry_run=dry_run,
        host=host,
        gateway_profile=gateway_profile,
        created_at=created_at,
    )
    doc["status"] = "failed"
    doc["verification"] = {"status": "FAIL"}
    doc["checks"].append({"id": "components", "status": "FAIL", "detail": detail})
    doc["error"] = {"kind": kind, "detail": detail, "rejected": rejected}
    doc["lifecycle"] = _receipt_lifecycle(doc)
    return doc


def install(
    *,
    scope: str = "project",
    root: Path | None = None,
    profile: str = "recommended",
    components: tuple[str, ...] | None = None,
    dry_run: bool = False,
    yes: bool = False,
    host: str | None = None,
    gateway_profile: str | None = None,
) -> dict[str, Any]:
    """Plan (dry_run) or apply the install. Returns a receipt doc.

    ``root`` is the install TARGET root (the-forge delegates
    ``--root <target>``); it is never a state dir — ``atlas.db`` and all
    service state live in the XDG data dir, outside every repo root.
    ``host``/``gateway_profile`` are accepted and honestly ignored: a
    service forge installs no host payloads and runs no execution
    profile.
    """
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r} (valid: {list(SCOPES)})")
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r} (valid: {list(PROFILES)})")
    created_at = _utc_now()
    resolved = resolve_root(str(root) if root else None) if scope != "user" else None
    if resolved is not None and not resolved.is_dir():
        raise ValueError(f"root {resolved} is not a directory")

    # Orchestrator-style component names get a typed refusal, not a
    # traceback-adjacent exit — the receipt stays a valid
    # forge/InstallReceipt/v1 and says exactly what was rejected.
    unknown = [c for c in (components or ()) if c not in COMPONENTS]
    if unknown:
        return _refusal_doc(
            kind=E_UNSUPPORTED_COMPONENTS,
            detail=(
                f"unknown components: {unknown} (valid: {list(COMPONENTS)}) — "
                "code-atlas is a service forge; content components "
                "(skills/agents/rules) are not installable"
            ),
            rejected=list(unknown),
            scope=scope,
            profile=profile,
            components=components,
            resolved=resolved,
            dry_run=dry_run,
            host=host,
            gateway_profile=gateway_profile,
            created_at=created_at,
        )
    comps = _profile_components(profile, components)
    members, ws_identity, ws_name = _workspace_surface(scope, resolved)

    receipt_name = _receipt_filename(created_at)
    steps = _plan(
        scope=scope,
        root=resolved,
        comps=comps,
        members=members,
        ws_name=ws_name,
        receipt_name=receipt_name,
    )
    doc = _base_doc(
        scope=scope,
        profile=profile,
        comps=comps,
        resolved=resolved,
        dry_run=dry_run,
        host=host,
        gateway_profile=gateway_profile,
        created_at=created_at,
    )
    doc["plan"] = steps
    doc["planned_files"] = _planned_files(steps, resolved)
    if scope == "workspace":
        doc["workspace"] = {
            "root": str(resolved),
            "identity_key": ws_identity,
            "federation_name": ws_name,
            "members_discovered": len(members),
        }
    if dry_run:
        doc["lifecycle"] = _receipt_lifecycle(doc)
        return doc
    if not yes:
        # Approval-gate refusal — ``blocked`` is the v1 receipt status a
        # reader derives the ``blocked`` lifecycle state from (was
        # ``declined`` pre-16.1; ``_BLOCKED_STATUSES`` still accepts it).
        doc = {
            **doc,
            "status": "blocked",
            "error": {"kind": E_INSTALL_DECLINED, "detail": "needs --yes - nothing written"},
        }
        doc["lifecycle"] = _receipt_lifecycle(doc)
        return doc
    return _apply_install(doc, steps, members, resolved, scope, ws_identity)


def _apply_install(
    doc: dict[str, Any],
    steps: list[dict[str, Any]],
    members: list[Path],
    resolved: Path | None,
    scope: str,
    ws_identity: str | None,
) -> dict[str, Any]:
    """Apply path: run plan steps, write durable artifacts, then emit
    receipts — per-target files first, the global doc last."""
    ctx = _ApplyCtx(resolved=resolved, scope=scope, ws_identity=ws_identity)
    ctx.checks = doc["checks"]  # keeps the host-gateway NOT_APPLICABLE seed
    for m in members:
        ctx.member_results[str(m)] = {"root": str(m), "name": m.name, "status": "discovered"}
    try:
        _apply_steps(steps, ctx)
        # gitignore files are durable managed artifacts — write them
        # before assembling the doc so sha256s are real.
        for s in steps:
            if s["component"] == "receipt" and s["action"] == "write-if-absent":
                _apply_gitignore_step(s, ctx)
    finally:
        ctx.close()

    doc["applied"] = ctx.applied
    doc["skipped"] = ctx.skipped
    doc["unverified"] = ctx.unverified
    doc["checks"] = ctx.checks
    doc["managed_files"] = ctx.managed
    doc["members"] = list(ctx.member_results.values())
    doc["federation"] = ctx.fed
    doc["installed_at"] = time.time()
    doc["verification"] = {"status": _aggregate(ctx.checks)}
    doc["receipt_id"] = (
        "sha256:" + hashlib.sha256(json.dumps(ctx.managed, sort_keys=True).encode()).hexdigest()
    )

    # -- per-target receipts: the ``.<state>/receipts`` convention ------
    _write_target_receipts(steps, doc, ctx)
    # -- the global receipt is persisted last; it records the final doc -
    _persist_global_receipt(doc)
    # -- the returned doc always carries the final derived state -------
    doc["lifecycle"] = _receipt_lifecycle(doc)
    return doc


def _workspace_surface(
    scope: str, resolved: Path | None
) -> tuple[list[Path], str | None, str | None]:
    """``(members, ws_identity, federation_name)`` for workspace scope —
    empty elsewhere."""
    if scope != "workspace" or resolved is None:
        return [], None, None
    from code_atlas.discovery.identity import identity_key_for_root

    identity = identity_key_for_root(resolved)
    return discover_projects(resolved), identity, _federation_name(identity)


def _unclaimed_receipt_path(path: Path) -> Path:
    """A receipt filename that is not already taken — prior receipt
    evidence is never overwritten (two installs inside the same second
    collide on the timestamp name, so the newer one gets ``-NN``)."""
    if not path.exists():
        return path
    for n in range(1, 100):
        cand = path.with_name(f"{path.stem}-{n:02d}{path.suffix}")
        if not cand.exists():
            return cand
    return path.with_name(f"{path.stem}-{time.time_ns()}{path.suffix}")


def _write_target_receipts(
    steps: list[dict[str, Any]], doc: dict[str, Any], ctx: _ApplyCtx
) -> None:
    """Write each planned ``.<state>/receipts/install-*.json`` — the ws
    root gets the consolidated doc, each member its own evidence."""
    target_docs = _target_docs(doc, ctx)
    for s in steps:
        if s["component"] != "receipt" or s["action"] != "write":
            continue
        tdoc = target_docs.get(str(s.get("receipt_target") or "")) or doc
        tdoc["lifecycle"] = _receipt_lifecycle(tdoc)
        try:
            p = _unclaimed_receipt_path(Path(s["target"]))
            tdoc["receipt"] = str(p)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(tdoc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            ctx.applied.append({**s, "result": str(p)})
            ctx.checks.append(
                {"id": f"receipt:{p.name}", "status": "PASS", "detail": s["target"]}
            )
            doc["receipt_files"].append(str(p))
        except OSError as exc:
            ctx.unverified.append({**s, "result": f"unverified:{exc}"})
            ctx.checks.append(
                {"id": f"receipt:{s['target']}", "status": "UNVERIFIED", "detail": str(exc)}
            )
            doc["verification"] = {"status": "UNVERIFIED"}


def _persist_global_receipt(doc: dict[str, Any]) -> None:
    """The data-dir receipt — last write, so it records its own path and
    the per-target results; a failure lands in ``unverified``."""
    doc["receipt"] = str(_receipt_path())
    try:
        _write_global_receipt(doc)
        doc["applied"].append(
            {
                "component": "receipt",
                "action": "write",
                "target": str(_receipt_path()),
                "result": str(_receipt_path()),
            }
        )
        doc["checks"].append(
            {"id": "receipt:global", "status": "PASS", "detail": str(_receipt_path())}
        )
        _write_global_receipt(doc)
    except OSError as exc:
        doc["unverified"].append(
            {
                "component": "receipt",
                "action": "write",
                "target": str(_receipt_path()),
                "result": f"unverified:{exc}",
            }
        )
        doc["checks"].append({"id": "receipt:global", "status": "UNVERIFIED", "detail": str(exc)})
        doc["verification"] = {"status": "UNVERIFIED"}


def _aggregate(checks: list[dict[str, Any]]) -> str:
    """Check statuses → ``verification.status``: FAIL beats UNVERIFIED
    beats PASS; NOT_APPLICABLE never blocks a PASS."""
    if any(c["status"] == "FAIL" for c in checks):
        return "FAIL"
    if any(c["status"] == "UNVERIFIED" for c in checks):
        return "UNVERIFIED"
    return "PASS"


class _ApplyCtx:
    """Apply-time shared state: resolved root, lazy atlas.db handle,
    federation workspace row, per-member outcomes."""

    def __init__(self, *, resolved: Path | None, scope: str, ws_identity: str | None) -> None:
        self.resolved = resolved
        self.scope = scope
        self.ws_identity = ws_identity
        # Ownership evidence from the *previous* global receipt — what an
        # incremental reinstall may carry forward as ``unchanged`` (§7.4).
        prior = read_receipt()
        self.prior_managed = _prior_managed_map(prior)
        self.prior_federation = (
            prior.get("federation") if isinstance(prior, dict) else None
        )
        self.conn: sqlite3.Connection | None = None
        self.fed: dict[str, Any] | None = None
        self.applied: list[dict[str, Any]] = []
        self.skipped: list[dict[str, Any]] = []
        self.unverified: list[dict[str, Any]] = []
        self.checks: list[dict[str, Any]] = []
        self.managed: list[dict[str, Any]] = []
        self.member_results: dict[str, dict[str, Any]] = {}

    def db(self) -> sqlite3.Connection:
        if self.conn is None:
            from code_atlas.storage import connect, migrate

            self.conn = connect(data_dir() / "atlas.db")
            migrate(self.conn)
        return self.conn

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None


def _check_id(s: dict[str, Any]) -> str:
    member = f"member:{s['member']}:" if s.get("member") else ""
    return f"{member}{s['component']}:{s['action']}"


def _apply_steps(steps: list[dict[str, Any]], ctx: _ApplyCtx) -> None:
    """Run all plan steps except receipts — those record what applied.

    A member step's failure lands in ``unverified`` and never sinks the
    batch: one bad member must not silently pass nor abort the others.
    """
    for s in steps:
        if s["component"] == "receipt":
            continue  # written later — it records what actually applied
        try:
            bucket, result = _apply_step(s, ctx)
        except (OSError, sqlite3.Error) as exc:
            _step_unverified(s, ctx, exc)
            continue
        except Exception as exc:  # member isolation — surface, don't crash
            if not s.get("member"):
                raise
            _step_unverified(s, ctx, exc)
            continue
        (ctx.applied if bucket == "applied" else ctx.skipped).append({**s, "result": result})
        ctx.checks.append({"id": _check_id(s), "status": "PASS", "detail": result})


def _step_unverified(s: dict[str, Any], ctx: _ApplyCtx, exc: Exception) -> None:
    ctx.unverified.append({**s, "result": f"unverified:{exc}"})
    ctx.checks.append({"id": _check_id(s), "status": "UNVERIFIED", "detail": str(exc)})
    if s.get("member") and s["target"] in ctx.member_results:
        ctx.member_results[s["target"]]["status"] = "unverified"
        ctx.member_results[s["target"]]["detail"] = str(exc)


def _apply_step(s: dict[str, Any], ctx: _ApplyCtx) -> tuple[str, str]:
    """One plan step -> ("applied"|"skipped", result). May raise."""
    comp, action, target = s["component"], s["action"], s["target"]
    if action == "skipped":
        return "skipped", target
    if action == "mkdir":
        Path(target).mkdir(parents=True, exist_ok=True)
        return "applied", "ok"
    if action == "write-if-absent" and comp == "config":
        if ctx.resolved is None:
            return "skipped", "user-scope"
        cfg = Path(target)
        if cfg.exists():
            # §7.4 incremental reinstall — an existing config is never
            # rewritten. It stays managed only when the previous receipt
            # proves install created it unchanged; a foreign or drifted
            # config is preserved and *not* adopted.
            if _prior_unchanged(ctx, cfg):
                ctx.managed.append(
                    {
                        "path": _rel(ctx.resolved, cfg),
                        "sha256": ctx.prior_managed[str(cfg.resolve())],
                        "kind": "config",
                        "action": "unchanged",
                    }
                )
                return "skipped", "exists — unchanged (install-managed)"
            return "skipped", "exists — preserved (not proven install-owned)"
        try:
            cfg = write_config(ctx.resolved)  # raises if config exists
        except ConfigError as exc:
            return "skipped", str(exc)
        ctx.managed.append(
            {
                "path": _rel(ctx.resolved, cfg),
                "sha256": _sha_file(cfg),
                "kind": "config",
                "action": "created",
            }
        )
        return "applied", "written"
    if action == "ensure-workspace":
        return _ensure_federation(ctx)
    if action == "register" and comp == "repository":
        return _register_step(ctx, Path(target))
    return "skipped", "no-handler"


def _ensure_federation(ctx: _ApplyCtx) -> tuple[str, str]:
    """Create-or-adopt the federation workspace named by the root's
    ``identity_key`` — the stable workspace identity. Name == identity,
    so a same-named row IS the same workspace (adoption, not collision).
    """
    from code_atlas.federation.store import FederationStore

    if ctx.ws_identity is None:
        return "skipped", "no workspace identity"
    name = _federation_name(ctx.ws_identity)
    conn = ctx.db()
    fstore = FederationStore(conn)
    ws = fstore.workspace_by_name(name)
    row_created = ws is None
    ws_id = fstore.create_workspace(name) if ws is None else ws.id
    # §7.4: an incremental reinstall adopts the existing row — but the
    # prior receipt may prove install created it, in which case the
    # workspace AND its install-added members stay install-owned (the
    # uninstall ledger is the latest receipt; losing `created` here
    # would leak the group and its rows).
    owned = row_created
    inherited_ids: list[int] = []
    prior = ctx.prior_federation
    if (
        not row_created
        and isinstance(prior, dict)
        and prior.get("created")
        and prior.get("workspace_id") == ws_id
    ):
        owned = True
        inherited_ids = [
            int(r) for r in (prior.get("member_repository_ids") or []) if isinstance(r, int)
        ]
    conn.commit()
    ctx.fed = {
        "workspace_id": ws_id,
        "name": name,
        # ``created`` reports this run; ``install_owned`` is the
        # provenance uninstall needs — a row created by an earlier
        # install stays ours to remove.
        "created": row_created,
        "install_owned": owned,
        "member_repository_ids": inherited_ids,
    }
    if row_created:
        detail = "created"
    elif owned:
        detail = "existing — still install-owned"
    else:
        detail = "existing"
    return ("applied" if row_created else "skipped"), (
        f"federation_workspace_id={ws_id} name={name} ({detail})"
    )


def _register_step(ctx: _ApplyCtx, member: Path) -> tuple[str, str]:
    """Register one repository under its stable identity — idempotent
    (``get_or_create_repository`` never duplicates an identity). Under
    workspace scope the member also joins the federation group. Never
    indexes."""
    from code_atlas.discovery.identity import identity_key_for_root
    from code_atlas.federation.store import FederationStore
    from code_atlas.storage.store import Store

    conn = ctx.db()
    store = Store(conn)
    ikey = identity_key_for_root(member)
    existed = store.repository_id_for(ikey) is not None
    repo_id = store.get_or_create_repository(ikey, str(member))
    detail = f"repository_id={repo_id} ({'existing' if existed else 'registered'})"
    if ctx.fed is not None:
        fstore = FederationStore(conn)
        already = fstore.member_for(ctx.fed["workspace_id"], repo_id) is not None
        if not already:
            fstore.add_member(ctx.fed["workspace_id"], repo_id)
            ctx.fed.setdefault("member_repository_ids", []).append(repo_id)
        detail += (
            f"; federation member of {ctx.fed['name']}#{ctx.fed['workspace_id']}"
            f" ({'existing' if already else 'added'})"
        )
    conn.commit()
    entry = ctx.member_results.setdefault(str(member), {"root": str(member)})
    entry.update(
        {
            "name": member.name,
            "identity_key": ikey,
            "repository_id": repo_id,
            "status": "existing" if existed else "registered",
        }
    )
    return ("skipped" if existed else "applied"), detail


def _apply_gitignore_step(s: dict[str, Any], ctx: _ApplyCtx) -> None:
    """``<target>/.code-atlas/.gitignore`` — write-if-absent, real sha256;
    a pre-existing file (user or foreign) is preserved, never adopted —
    unless the previous receipt proves install wrote it unchanged, in
    which case the reinstall carries it forward as ``unchanged``."""
    p = Path(s["target"])
    target_root = Path(s["receipt_target"])
    try:
        if p.exists() and _prior_unchanged(ctx, p):
            ctx.skipped.append({**s, "result": "exists — unchanged (install-managed)"})
            ctx.checks.append(
                {
                    "id": f"file:{p.parent.name}/{p.name}",
                    "status": "PASS",
                    "detail": "unchanged — still managed (prior receipt proof)",
                }
            )
            ctx.managed.append(
                {
                    "path": _rel(target_root, p),
                    "sha256": ctx.prior_managed[str(p.resolve())],
                    "kind": "config",
                    "action": "unchanged",
                }
            )
            return
        if p.exists():
            ctx.skipped.append({**s, "result": "exists"})
            ctx.checks.append(
                {
                    "id": f"file:{p.parent.name}/{p.name}",
                    "status": "PASS",
                    "detail": "preserved existing (not adopted)",
                }
            )
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        # bytes, not text — text-mode would emit `*\r\n` on Windows and the
        # managed sha256 would diverge per platform for identical content.
        p.write_bytes(_GITIGNORE_PAYLOAD.encode("utf-8"))
    except OSError as exc:
        ctx.unverified.append({**s, "result": f"unverified:{exc}"})
        ctx.checks.append(
            {"id": f"file:{p.name}", "status": "UNVERIFIED", "detail": str(exc)}
        )
        return
    ctx.applied.append({**s, "result": "written"})
    ctx.checks.append({"id": f"file:{p.name}", "status": "PASS", "detail": str(p)})
    ctx.managed.append(
        {
            "path": _rel(target_root, p),
            "sha256": _sha_file(p),
            "kind": "config",
            "action": "created",
        }
    )


def _federation_name(identity_key: str) -> str:
    """Deterministic federation workspace name: the member root's
    ``identity_key`` truncated to the name limit with a hash suffix —
    the same root always derives the same name."""
    if len(identity_key) <= _WS_NAME_LIMIT:
        return identity_key
    digest = hashlib.sha256(identity_key.encode()).hexdigest()[:12]
    return f"{identity_key[: _WS_NAME_LIMIT - 14]}-{digest}"


def _planned_files(steps: list[dict[str, Any]], target_root: Path | None) -> list[dict[str, Any]]:
    """``planned_files`` for plan evidence — install-owned writes."""
    out: list[dict[str, Any]] = []
    for s in steps:
        if s["component"] == "receipt" and s["action"] in ("write", "write-if-absent"):
            p = Path(s["target"])
            exists = p.is_file()
            out.append(
                {
                    "path": _rel(target_root, p),
                    "kind": "config" if p.name == ".gitignore" else "state",
                    "action": "preserve" if exists else "create",
                    "sha256": _sha_file(p) if exists else "",
                }
            )
        elif s["component"] == "config" and s["action"] == "write-if-absent":
            p = Path(s["target"])
            exists = p.is_file()
            out.append(
                {
                    "path": str(p),
                    "kind": "config",
                    "action": "preserve" if exists else "create",
                    "sha256": _sha_file(p) if exists else "",
                }
            )
    return out


def _target_docs(doc: dict[str, Any], ctx: _ApplyCtx) -> dict[str, dict[str, Any]]:
    """Receipt doc per install target — the ws/project root gets the
    consolidated doc; each member gets a standalone receipt carrying its
    own registration evidence (status mirrors the member outcome)."""
    docs: dict[str, dict[str, Any]] = {}
    if ctx.resolved is not None:
        docs[str(ctx.resolved)] = doc
    for root, res in ctx.member_results.items():
        status = res.get("status")
        passed = status in ("registered", "existing")
        member_doc = {
            **{k: v for k, v in doc.items() if k not in ("checks", "verification", "status")},
            "target_root": root,
            "member": dict(res),
            "checks": [
                {
                    "id": "member:register",
                    "status": "PASS" if passed else "UNVERIFIED",
                    "detail": (
                        f"repository_id={res['repository_id']} ({status})"
                        if res.get("repository_id") is not None
                        else str(res.get("detail") or status)
                    ),
                }
            ],
            "verification": {"status": "PASS" if passed else "UNVERIFIED"},
            "status": "completed" if passed else "failed",
            "members": [],
            "plan": [],
            "applied": [],
            "skipped": [],
            "unverified": [],
        }
        docs[root] = member_doc
    return docs


def _write_global_receipt(doc: dict[str, Any]) -> str:
    # persisted evidence always carries the state derived at write time
    doc["lifecycle"] = _receipt_lifecycle(doc)
    p = _receipt_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return str(p)


def _owned_components(receipt: dict[str, Any] | None) -> set[str]:
    """Component names the receipt proves install applied — accepts the
    current step-dict ``applied`` and the legacy name-list shape, plus
    the legacy requested-``components`` fallback."""
    owned: set[str] = set()
    if not receipt:
        return owned
    applied = receipt.get("applied") or []
    for s in applied:
        if isinstance(s, dict) and s.get("component"):
            owned.add(str(s["component"]))
        elif isinstance(s, str):
            owned.add(s)
    comps = receipt.get("components")
    if not owned and comps:
        if isinstance(comps, dict):
            comps = comps.get("selected") or []
        owned.update(str(c) for c in comps if isinstance(c, str))
    return owned


def uninstall(
    *,
    scope: str = "project",
    root: Path | None = None,
    dry_run: bool = False,
    yes: bool = False,
    purge_state: bool = False,
) -> dict[str, Any]:
    """Remove what install owns — honestly bounded.

    The receipt is the proof of what install applied: ``config.toml``
    only leaves when the receipt says install *wrote* it (never a config
    the user authored); ``<target>/.code-atlas/`` managed files only
    leave for roots the receipt names; a federation group only leaves
    when the receipt says install *created* it and it still holds only
    install-registered members. ``atlas.db``/index state only goes with
    ``purge_state`` — deleting indexed data is a separate, irreversible
    decision, not a default.
    """
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r} (valid: {list(SCOPES)})")
    resolved = resolve_root(str(root) if root else None) if scope != "user" else None
    steps = _uninstall_plan(scope=scope, resolved=resolved, purge_state=purge_state)
    doc: dict[str, Any] = {
        "schema": SCHEMA_RECEIPT,
        "forge_id": FORGE_ID,
        "operation": "uninstall",
        "status": "planned" if dry_run else "completed",
        "scope": scope,
        "target_root": str(resolved) if resolved else None,
        "purge_state": purge_state,
        "plan": steps,
        "managed_files": [],
        "applied": [],
        "skipped": [],
        "unverified": [],
        "checks": [],
        "verification": {"status": "UNVERIFIED"},
        "created_at": _utc_now(),
        "created_by": f"code-atlas-forge/{__version__}",
        "version": __version__,
    }
    if dry_run:
        doc["lifecycle"] = _receipt_lifecycle(doc)
        return doc
    if not yes:
        doc = {
            **doc,
            "status": "blocked",
            "error": {"kind": E_UNINSTALL_DECLINED, "detail": "needs --yes - nothing removed"},
        }
        doc["lifecycle"] = _receipt_lifecycle(doc)
        return doc

    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    unverified: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    for s in steps:
        try:
            bucket, result = _remove_step(s)
        except OSError as exc:
            unverified.append({**s, "result": f"unverified:{exc}"})
            checks.append({"id": _check_id(s), "status": "UNVERIFIED", "detail": str(exc)})
            continue
        (applied if bucket == "applied" else skipped).append({**s, "result": result})
        checks.append({"id": _check_id(s), "status": "PASS", "detail": result})
    doc["applied"] = applied
    doc["skipped"] = skipped
    doc["unverified"] = unverified
    doc["checks"] = checks
    doc["verification"] = {"status": _aggregate(checks)}
    doc["lifecycle"] = _receipt_lifecycle(doc)
    return doc


def _uninstall_plan(
    *, scope: str, resolved: Path | None, purge_state: bool
) -> list[dict[str, Any]]:
    receipt = read_receipt()
    steps: list[dict[str, Any]] = []
    owned = _owned_components(receipt)
    cfg = config_file()
    # Ownership proof: the applied-component set (legacy receipts) or a
    # managed_files entry resolving to the config — ``unchanged``
    # entries from incremental reinstalls count (they are still ours).
    cfg_sha = _managed_sha_for(receipt, cfg)
    if scope in ("project", "workspace") and resolved is not None:
        if receipt and ("config" in owned or cfg_sha is not None):
            steps.append(
                {
                    "component": "config",
                    "action": "remove",
                    "target": str(cfg),
                    "sha256": cfg_sha,
                }
            )
        else:
            steps.append(
                {
                    "component": "config",
                    "action": "skipped",
                    "target": "no receipt proof install wrote it — kept",
                }
            )
    # Per-target `.code-atlas` evidence + federation — receipt-proven.
    steps.extend(_uninstall_managed_steps(receipt, scope=scope, resolved=resolved))
    if purge_state:
        steps.append(
            {"component": "atlas.db", "action": "remove", "target": str(data_dir() / "atlas.db")}
        )
    steps.append({"component": "receipt", "action": "remove", "target": str(_receipt_path())})
    return steps


def _uninstall_managed_steps(
    receipt: dict[str, Any] | None, *, scope: str, resolved: Path | None
) -> list[dict[str, Any]]:
    """Per-target state + federation steps — only what the receipt proves."""
    steps: list[dict[str, Any]] = []
    targets: set[str] = set()
    if scope in ("project", "workspace"):
        if resolved is not None:
            targets.add(str(resolved))
        if isinstance(receipt, dict):
            tr = receipt.get("target_root")
            if isinstance(tr, str):
                targets.add(tr)
            for m in receipt.get("members") or []:
                if isinstance(m, dict) and isinstance(m.get("root"), str):
                    targets.add(m["root"])
    for t in sorted(targets):
        steps.append(
            {
                "component": "target-state",
                "action": "remove-managed",
                "target": str(state_dir(Path(t))),
            }
        )
    # Federation group — only when the receipt proves install owns it
    # (``install_owned``; legacy receipts carry only ``created``).
    fed = receipt.get("federation") if isinstance(receipt, dict) else None
    if (
        isinstance(fed, dict)
        and bool(fed.get("install_owned", fed.get("created")))
        and isinstance(fed.get("workspace_id"), int)
    ):
        steps.append(
            {
                "component": "federation",
                "action": "remove-managed",
                "target": str(fed.get("name") or fed["workspace_id"]),
                "ws_id": int(fed["workspace_id"]),
                "repo_ids": [
                    int(r) for r in (fed.get("member_repository_ids") or []) if isinstance(r, int)
                ],
            }
        )
    return steps


def _remove_config_step(p: Path, expected_sha: str | None) -> tuple[str, str]:
    """Drift-aware config removal: the receipt proves install wrote it,
    but a user edit since then makes it foreign content — kept, never
    deleted. ``sha256=""``/missing means owned without a recorded hash."""
    if expected_sha:
        try:
            actual = _sha_file(p) if p.is_file() else None
        except OSError:
            actual = None
        if actual != expected_sha:
            return "skipped", "kept — modified since install"
    if p.exists():
        p.unlink()
        return "applied", "removed"
    return "skipped", "absent"


def _remove_step(s: dict[str, Any]) -> tuple[str, str]:
    """One uninstall step -> ("applied"|"skipped", result)."""
    if s["action"] == "skipped":
        return "skipped", s["target"]
    p = Path(s["target"])
    if s["component"] == "config":
        return _remove_config_step(p, s.get("sha256"))
    if s["component"] == "receipt":
        # receipt dir goes wholesale — install/ holds only receipts
        receipt_dir = p.parent
        if receipt_dir.is_dir():
            shutil.rmtree(receipt_dir)
            return "applied", "removed"
        return "skipped", "absent"
    if s["component"] == "target-state":
        return _remove_target_state(p)
    if s["component"] == "federation":
        return _remove_federation(int(s["ws_id"]), [int(r) for r in s.get("repo_ids") or []])
    if p.exists():
        p.unlink()
        return "applied", "removed"
    return "skipped", "absent"


def _remove_target_state(state: Path) -> tuple[str, str]:
    """Remove only install-managed files under ``<target>/.code-atlas`` —
    receipt JSONs, our exact ``.gitignore`` payload; the dirs leave only
    when nothing else remains (user files always win)."""
    if not state.exists():
        return "skipped", "absent"
    removed: list[str] = []
    kept: list[str] = []
    _remove_receipt_files(state / RECEIPTS_DIRNAME, removed, kept)
    _remove_owned_gitignore(state / ".gitignore", removed, kept)
    try:
        state.rmdir()
    except OSError:
        kept.append(state.name)
    if removed and not kept:
        return "applied", f"removed {removed}"
    if removed and kept:
        return "applied", f"removed {removed}; kept {kept}"
    return "skipped", f"no managed files — kept {kept or 'dir'}"


def _remove_receipt_files(rdir: Path, removed: list[str], kept: list[str]) -> None:
    if not rdir.is_dir():
        return
    for f in sorted(rdir.glob("*.json")):
        f.unlink()
        removed.append(f.name)
    try:
        rdir.rmdir()
    except OSError:
        kept.append(RECEIPTS_DIRNAME)


def _remove_owned_gitignore(gi: Path, removed: list[str], kept: list[str]) -> None:
    if not gi.is_file():
        return
    try:
        own = gi.read_text(encoding="utf-8") == _GITIGNORE_PAYLOAD
    except OSError:
        own = False
    if own:
        gi.unlink()
        removed.append(".gitignore")
    else:
        kept.append(".gitignore")


def _remove_federation(ws_id: int, repo_ids: list[int]) -> tuple[str, str]:
    """Remove install-added member rows; the workspace row leaves only
    when it holds no other members — a user-extended group is kept."""
    from code_atlas.federation.store import FederationStore
    from code_atlas.storage import connect

    db = data_dir() / "atlas.db"
    if not db.exists():
        return "skipped", "no atlas.db"
    conn = connect(db)
    try:
        fstore = FederationStore(conn)
        if fstore.workspace_by_id(ws_id) is None:
            return "skipped", "absent"
        removed = 0
        for rid in repo_ids:
            if fstore.remove_member(ws_id, rid):
                removed += 1
        if fstore.member_count(ws_id) == 0:
            fstore.delete_workspace(ws_id)
            conn.commit()
            return "applied", f"removed workspace {ws_id} + {removed} members"
        conn.commit()
        return "applied", f"removed {removed} members; workspace kept (other members remain)"
    finally:
        conn.close()


def _receipt_state() -> str:
    """ok | missing | error — a corrupt receipt is a real state, not 'missing'."""
    p = _receipt_path()
    if not p.exists():
        return "missing"
    return "ok" if read_receipt() is not None else "error"


def status() -> dict[str, Any]:
    """Ledger + receipt + drift summary — ``forge/InstallationHealth/v1``.

    Health derives from real evidence: a missing receipt is
    ``unverified`` (no proof either way), a corrupt one ``broken``; a
    readable receipt plus on-disk drift over ``managed_files`` maps to
    ``healthy``/``degraded``/``broken`` (all managed drifted)."""
    r = read_receipt()
    r_state = _receipt_state()
    drift = _managed_drift(r)
    ok = sum(1 for d in drift if d["status"] == "ok")
    bad = [d for d in drift if d["status"] != "ok"]
    if r_state == "missing":
        health = "unverified"
    elif r_state == "error" or (drift and not ok):
        health = "broken"
    elif bad:
        health = "degraded"
    else:
        health = "healthy"
    return {
        "schema": SCHEMA_HEALTH,
        "forge_id": FORGE_ID,
        "status": health,
        "lifecycle": _health_lifecycle(health),
        "installed": r is not None,
        "receipt_state": r_state,
        "drift": {
            "ok": ok,
            "modified": [d["path"] for d in bad if d["status"] == "modified"],
            "missing": [d["path"] for d in bad if d["status"] == "missing"],
        },
        # Observable context budget only (contract §7.1): the managed
        # entries the receipt actually lists and the tools the MCP
        # server enumerates — nothing inferred.
        "context": {
            "managed_entries": len((r or {}).get("managed_files") or []),
            "tools_exposed": _tool_count(),
        },
        "receipt": r,
        "checked_at": _utc_now(),
    }


def doctor(*, scope: str = "project", root: Path | None = None) -> dict[str, Any]:
    """Health check — ``forge/InstallationHealth/v1``-aligned probes:
    receipt readable, data dir writable, db openable, plus a derived
    ``forge/InstallLifecycle/v1`` block."""
    checks: list[dict[str, Any]] = []
    r_state = _receipt_state()
    checks.append(
        {
            "id": "receipt",
            "status": {"ok": "PASS", "missing": "UNVERIFIED", "error": "FAIL"}[r_state],
            "detail": str(_receipt_path()),
            "repairable": r_state != "ok",
        }
    )
    dd = data_dir()
    try:
        dd.mkdir(parents=True, exist_ok=True)
        probe = dd / ".atlas-probe"
        probe.write_text("ok")
        probe.unlink()
        dd_state = "ok"
    except OSError:
        dd_state = "unwritable"
    checks.append(
        {
            "id": "data-dir",
            "status": "PASS" if dd_state == "ok" else "FAIL",
            "detail": str(dd),
            "repairable": False,
        }
    )
    cfg = config_file()
    if scope in ("project", "workspace"):
        checks.append(
            {
                "id": "config",
                "status": "PASS" if cfg.exists() else "UNVERIFIED",
                "detail": str(cfg),
                "repairable": True,
            }
        )
    resolved = (
        resolve_root(str(root) if root else None) if scope in ("project", "workspace") else None
    )
    if resolved is not None and resolved.is_dir():
        rdir = receipts_dir(resolved)
        checks.append(
            {
                "id": "target-receipt",
                "status": "PASS" if rdir.is_dir() and any(rdir.glob("*.json")) else "UNVERIFIED",
                "detail": str(rdir),
                "repairable": True,
            }
        )
    db = dd / "atlas.db"
    try:
        conn = sqlite3.connect(db)
        conn.execute("SELECT 1")
        conn.close()
        db_state = "ok"
    except sqlite3.Error:
        db_state = "error"
    checks.append(
        {
            "id": "database",
            "status": "PASS" if db_state == "ok" else "FAIL",
            "detail": str(db),
            "repairable": True,
        }
    )
    checks.append(
        {
            "id": "python>=3.12",
            "status": "PASS" if sys.version_info >= (3, 12) else "FAIL",
            "detail": sys.version.split()[0],
            "repairable": False,
        }
    )
    # Same aggregation the-forge applies: any FAIL degrades — but only
    # when there is install evidence; without a readable receipt a FAIL
    # means ``broken`` and no FAIL means ``unverified``.
    installed = r_state == "ok"
    if any(c["status"] == "FAIL" for c in checks):
        overall = "degraded" if installed else "broken"
    elif not installed:
        overall = "unverified"
    else:
        overall = "healthy"
    receipt = read_receipt() if installed else None
    return {
        "schema": SCHEMA_HEALTH,
        "forge_id": FORGE_ID,
        "scope": scope,
        "status": overall,
        "checks": checks,
        "lifecycle": _health_lifecycle(overall),
        # Observable context budget only (contract §7.1) — managed
        # entries the receipt lists; tools the MCP server enumerates.
        "context": {
            "managed_entries": (
                len(receipt.get("managed_files") or []) if isinstance(receipt, dict) else None
            ),
            "tools_exposed": _tool_count(),
        },
        "checked_at": _utc_now(),
    }


__all__ = [
    "COMPONENTS",
    "FORGE_ID",
    "PROFILES",
    "PROFILE_COMPONENTS",
    "SCHEMA_HEALTH",
    "SCHEMA_LIFECYCLE",
    "SCHEMA_RECEIPT",
    "SCOPES",
    "doctor",
    "install",
    "read_receipt",
    "status",
    "uninstall",
]
