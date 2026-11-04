"""Guided installation wizard (Experience Program §1.2).

Drives the *real* install service — every choice maps to an actual
``install()`` call; the review step is the real dry-run plan, and the
apply step is the governed ``--yes`` path. Nothing here writes files
itself.

Flow: env check → scope → profile → execution profile → components →
hosts → review (dry-run plan) → confirm → apply → doctor → finish.
Each optional step is skipped when its vocabulary is empty (a forge
with no gateway profiles, components or hosts simply never asks).
Any step can be cancelled (Esc/q) without side effects; nothing is
written before the explicit confirm.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

from code_atlas.ui import i18n
from code_atlas.ui.kit import (
    NonInteractive,
    UIContext,
    _c,
    confirm,
    dashboard,
    multi_select,
    select,
)

_ALL_HOSTS = ("claude", "devin", "codex", "copilot")
_SCOPES = ("project", "workspace", "user")
_SCOPE_LABELS = {
    "project": "Current repository",
    "workspace": "Entire workspace",
    "user": "My user account",
}
_PROFILES = ("recommended", "minimal", "full")  # service contract names
_PROFILE_LABELS = {
    "recommended": "Balanced (Recommended)",
    "minimal": "Economy",
    "full": "Full",
}
_GATEWAY_PROFILES = ("economy", "balanced", "deep")
_GATEWAY_LABELS = {
    "economy": "Economy (lowest context cost)",
    "balanced": "Balanced (recommended)",
    "deep": "Deep (maximum verification)",
}
_COMPONENTS = ("skills", "agents", "mcp", "tui", "graph-studio")
_COMPONENT_LABELS = {
    "skills": "Skills",
    "agents": "Agents",
    "mcp": "MCP",
    "tui": "TUI",
    "graph-studio": "Graph Studio",
}
_COMPONENT_DETAILS = {
    "skills": "host-discoverable skills and progressive capability guidance",
    "agents": "specialists, subagents and declared agent instructions",
    "mcp": "MCP configuration and governed server entry points",
    "tui": "the terminal command center runtime",
    "graph-studio": "federated graph views and local graph navigation",
}
_COMPONENT_DEFAULTS = {
    "minimal": {"mcp", "tui"},
    "recommended": {"skills", "agents", "mcp", "tui", "graph-studio"},
    "full": {"skills", "agents", "mcp", "tui", "graph-studio"},
}


def _has_vcs_root(start: Path) -> bool:
    """Return whether current directory belongs to a supported project root."""
    return any(
        (candidate / ".git").exists()
        or ((candidate / "forge.json").is_file() and (candidate / "pyproject.toml").is_file())
        for candidate in (start, *start.parents)
    )


def _scope_choices(ctx: UIContext, scopes: tuple[str, ...]) -> list[str]:
    """Hide repository-only choice when operator is in a fresh directory."""
    choices = list(scopes)
    if "project" in choices and not _has_vcs_root(Path.cwd()):
        print(
            _c(
                ctx,
                "33",
                "  No repository detected; choose Entire workspace to use this directory "
                "or My user account.",
            )
        )
        choices.remove("project")
    return choices


def env_check_rows(hosts: tuple[str, ...] = _ALL_HOSTS) -> list[tuple[str, str]]:
    """Real environment evidence: python, git, host binaries."""
    v = sys.version_info
    rows = [("Python", f"{v.major}.{v.minor}.{v.micro}")]
    rows.append(("Git", "OK" if shutil.which("git") else "missing"))
    for h in hosts:
        rows.append((h.capitalize(), "Available" if shutil.which(h) else "not detected"))
    return rows


def _banner(ctx: UIContext, forge_name: str) -> None:
    print()
    print(_c(ctx, "1", f" {forge_name.upper()}"))
    print(_c(ctx, "2", " " + ("─" if ctx.unicode else "-") * max(10, len(forge_name) + 4)))
    print(f"  {i18n.t('env_detected', ctx.language)}:")


def run_wizard(
    *,
    forge_name: str,
    install_fn: Any,
    doctor_fn: Any | None = None,
    hosts_available: tuple[str, ...] = _ALL_HOSTS,
    gateway_profiles: tuple[str, ...] = _GATEWAY_PROFILES,
    scopes: tuple[str, ...] = _SCOPES,
    component_order: tuple[str, ...] | None = _COMPONENTS,
    component_labels: dict[str, str] | None = None,
    component_details: dict[str, str] | None = None,
    component_defaults: dict[str, set[str]] | None = None,
    env_hosts: tuple[str, ...] | None = None,
    next_hint: str | None = None,
    ctx: UIContext | None = None,
    out_json: bool = False,
    family: bool = False,
) -> int:
    """Interactive install wizard. Returns exit code.

    ``install_fn(**kwargs)`` is the forge's real install service
    (``host``/``scope``/``profile``/``gateway_profile``/``components``/
    ``dry_run``/``yes`` kwargs). ``doctor_fn`` optionally verifies
    post-install. Empty vocabularies skip their step: no hosts, no
    gateway profile or no components means the question is never asked.
    """
    ctx = ctx or UIContext.detect()
    if not ctx.interactive:
        raise NonInteractive("install wizard requires a TTY")

    comp_order = component_order or ()
    comp_labels = component_labels or _COMPONENT_LABELS
    comp_details = component_details or _COMPONENT_DETAILS
    comp_defaults = component_defaults or _COMPONENT_DEFAULTS

    _banner(ctx, forge_name)
    env_rows = env_check_rows(hosts_available if env_hosts is None else env_hosts)
    for label, val in env_rows:
        print(f"    {label:<14} {_c(ctx, '32' if val in ('OK', 'Available') else '2', val)}")
    print()
    if family:
        print(_c(ctx, "36", "  family mode: The Forge + registered specialist forges"))
        print(_c(ctx, "2", "  one real plan, one approval, each forge keeps its own installer"))
        print()

    # --- scope ---------------------------------------------------------------
    scope_choices = _scope_choices(ctx, scopes)
    idx = select(
        i18n.t("install_scope_q", ctx.language),
        [_SCOPE_LABELS.get(s, s) for s in scope_choices],
        ctx=ctx,
    )
    if idx is None:
        return 130
    scope = scope_choices[idx]

    # --- profile -------------------------------------------------------------
    idx = select(
        i18n.t("install_profile_q", ctx.language),
        [_PROFILE_LABELS[p] for p in _PROFILES],
        ctx=ctx,
    )
    if idx is None:
        return 130
    profile = _PROFILES[idx]

    # --- execution profile ----------------------------------------------------
    gateway_profile: str | None = None
    if gateway_profiles:
        idx = select(
            i18n.t("install_gateway_profile_q", ctx.language),
            [_GATEWAY_LABELS.get(p, p) for p in gateway_profiles],
            ctx=ctx,
        )
        if idx is None:
            return 130
        gateway_profile = gateway_profiles[idx]

    # --- optional components ---------------------------------------------------
    components: tuple[str, ...] | None = None
    if comp_order:
        comp_checked = comp_defaults.get(profile, set())
        print(_c(ctx, "2", "  component choices (economy loads optional knowledge/evals on demand):"))
        for component in comp_order:
            detail = comp_details.get(component)
            print(f"    {component:<13} {detail}" if detail else f"    {component}")
        comp_chosen = multi_select(
            i18n.t("install_components_q", ctx.language),
            [comp_labels.get(c, c) for c in comp_order],
            checked={i for i, c in enumerate(comp_order) if c in comp_checked},
            ctx=ctx,
        )
        if comp_chosen is None:
            return 130
        components = tuple(sorted(comp_order[i] for i in comp_chosen))

    # --- hosts ---------------------------------------------------------------
    host_arg: str | None = None
    if hosts_available:
        detected = {
            i
            for i, h in enumerate(hosts_available)
            if any(h in r[0].lower() and r[1] == "Available" for r in env_rows)
        }
        chosen = multi_select(
            i18n.t("install_hosts_q", ctx.language),
            list(hosts_available),
            checked=detected,
            ctx=ctx,
        )
        if chosen is None:
            return 130
        if not chosen:
            host_arg = None
        elif len(chosen) == len(hosts_available):
            host_arg = "all"
        else:
            host_arg = ",".join(hosts_available[i] for i in sorted(chosen))

    # --- review (real dry-run plan) ------------------------------------------
    # Contrato explícito: install_fn aceita `components` — wrappers **kw
    # encaminham sem inspeção de assinatura (GAP-002).
    kwargs: dict[str, Any] = {
        "scope": scope,
        "profile": profile,
        "gateway_profile": gateway_profile,
        "components": components,
    }
    try:
        # `none` é opt-out explícito — zero hosts escolhidos nunca vira
        # "all" (GAP-003); csv de subconjunto é contrato válido (GAP-003b).
        plan = install_fn(host=host_arg or "none", dry_run=True, **kwargs)
    except Exception as exc:
        print(_c(ctx, "31", f"plan failed: {exc}"))
        return 1

    planned = (
        plan.get("planned")
        or plan.get("planned_files")
        or plan.get("writes")
        or plan.get("plan")
        or []
    )
    review_rows = [
        ("scope", scope),
        ("profile", profile),
    ]
    if gateway_profile is not None:
        review_rows.append(("execution", gateway_profile))
    review_rows.append(("mode", "family" if family else "this forge"))
    if comp_order:
        review_rows.append(("components", ", ".join(components or ()) or "none"))
    if hosts_available:
        review_rows.append(("hosts", host_arg or "none"))
    review_rows.append(
        ("managed writes", str(len(planned) if isinstance(planned, list) else planned))
    )
    dashboard(
        f"{forge_name} — {i18n.t('review', ctx.language)}",
        [("Plan", review_rows)],
        ctx=ctx,
    )
    if isinstance(planned, list):
        for entry in planned[:10]:
            if isinstance(entry, dict):
                path = str(entry.get("path") or entry.get("target") or "")
            else:
                path = str(entry)
            print(f"    {_c(ctx, '2', path)}")
        if len(planned) > 10:
            print(f"    … {len(planned) - 10} more")

    if not confirm(i18n.t("confirm_q", ctx.language), default=True, ctx=ctx):
        return 130

    # --- apply (real governed install) ---------------------------------------
    receipt = install_fn(host=host_arg or "none", yes=True, dry_run=False, **kwargs)
    status = receipt.get("status", "unknown")
    if out_json:
        print(json.dumps(receipt, indent=2, ensure_ascii=False))
    color = "32" if status == "completed" else "31"
    print(f"\n  {i18n.t('status', ctx.language)}: {_c(ctx, color, status)}")

    # --- verify --------------------------------------------------------------
    if doctor_fn is not None and status == "completed":
        health = doctor_fn(scope=scope)
        raw_health = health.get("status") or health.get("health") or "unknown"
        hstatus = raw_health
        if isinstance(raw_health, dict):
            hstatus = raw_health.get("status", "unknown")
        color = "32" if hstatus in ("healthy", "PASS") else "33"
        print(f"  {i18n.t('health', ctx.language)}: {_c(ctx, color, str(hstatus))}")

    if status == "completed" and next_hint:
        print(f"  next: {next_hint}")

    return 0 if status == "completed" else 1


__all__ = ["run_wizard", "env_check_rows", "NonInteractive", "UIContext"]
