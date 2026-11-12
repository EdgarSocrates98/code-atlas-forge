"""`code-atlas install` — guided wizard (TTY) + flag path (JSON).

Same UX contract as `sparkforge-aws install` / `productforge install`:
the shared ui/kit wizard drives scope -> install profile -> components
-> real dry-run plan -> confirm -> apply -> doctor. Atlas-honest
differences: no host payloads and no gateway profile (a local-first
engine, not a content forge), so those steps are skipped by passing
empty vocabularies to the wizard.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import lifecycle

_COMPONENT_LABELS = {
    "config": "Config",
    "data-dir": "Data dir",
    "repository": "Repository",
}
_COMPONENT_DETAILS = {
    "config": "workspace config.toml (schema_version + root)",
    "data-dir": "local data dir — atlas.db, receipts, manifests",
    "repository": "repo registered in the index database",
}


def _tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _install(**kw: Any) -> dict[str, Any]:
    """Map the wizard's keyword contract onto the real lifecycle.

    ``host``/``gateway_profile`` are accepted and ignored — the atlas
    installs no host payloads and has no execution profile.
    """
    return lifecycle.install(
        scope=kw.get("scope", "project"),
        profile=kw.get("profile", "recommended"),
        components=kw.get("components"),
        dry_run=bool(kw.get("dry_run")),
        yes=bool(kw.get("yes")),
    )


def _doctor(**kw: Any) -> dict[str, Any]:
    return lifecycle.doctor(scope=kw.get("scope", "project"))


def run_guided_install() -> int:
    """Interactive wizard via the vendored ui/kit — arrow-key menus."""
    from ..ui.kit import UIContext
    from ..ui.wizard import run_wizard

    return run_wizard(
        forge_name="code-atlas-forge",
        install_fn=_install,
        doctor_fn=_doctor,
        hosts_available=(),
        gateway_profiles=(),
        scopes=("project", "user"),
        component_order=lifecycle.COMPONENTS,
        component_labels=_COMPONENT_LABELS,
        component_details=_COMPONENT_DETAILS,
        component_defaults={p: set(c) for p, c in lifecycle.PROFILE_COMPONENTS.items()},
        env_hosts=(),
        next_hint="code-atlas doctor · code-atlas index",
        ctx=UIContext.detect(),
    )


def run_flag_install(
    *,
    scope: str,
    profile: str,
    components: str | None,
    root: str | None,
    dry_run: bool,
    yes: bool,
) -> dict[str, Any]:
    """Non-interactive flag path — returns the receipt doc."""
    comps = tuple(c.strip() for c in components.split(",") if c.strip()) if components else None
    return lifecycle.install(
        scope=scope,
        profile=profile,
        components=comps,
        root=Path(root) if root else None,
        dry_run=dry_run,
        yes=yes,
    )


def _render_install_doc(doc: dict[str, Any]) -> None:
    print(f"{doc.get('operation', 'install')}: {doc.get('status', '?')}")
    for s in doc.get("applied", []):
        print(f"  {s['component']:<10} {s['action']:<22} {s['target']}")
    for s in doc.get("skipped", []):
        print(f"  skipped    {s['component']:<10} {s.get('result', s['target'])}")
    for s in doc.get("unverified", []):
        print(f"  unverified {s['component']:<10} {s['result']}")
    if doc.get("status") == "planned":
        for s in doc.get("plan", []):
            print(f"  plan       {s['component']:<10} {s['action']:<22} {s['target']}")
    if doc.get("error"):
        print(f"  refused    {doc['error'].get('kind')}: {doc['error'].get('detail')}")


def _render_check_doc(doc: dict[str, Any]) -> None:
    for k, v in doc.items():
        if k == "checks":
            for c in v:
                print(f"  {c['state']:<10} {c['check']:<18} {c['target']}")
        elif k != "receipt":
            print(f"{k}: {v}")
    if doc.get("receipt") is not None:
        print(f"receipt: {json.dumps(doc['receipt'], ensure_ascii=False)}")


def run_install(args: Any) -> int:
    """argparse handler — ``install|status|doctor`` actions."""
    from code_atlas.observability.logging import escape_control

    action = getattr(args, "action", "install")
    try:
        if action == "status":
            doc = lifecycle.status()
        elif action == "doctor":
            doc = lifecycle.doctor(scope=args.scope)
        elif action == "uninstall":
            doc = lifecycle.uninstall(
                scope=args.scope,
                root=Path(args.root) if args.root else None,
                dry_run=args.dry_run,
                yes=args.yes,
                purge_state=getattr(args, "purge", False),
            )
        else:
            doc = run_flag_install(
                scope=args.scope,
                profile=args.profile,
                components=args.components,
                root=args.root,
                dry_run=args.dry_run,
                yes=args.yes,
            )
    except ValueError as exc:
        print(f"error: {escape_control(str(exc))}", file=sys.stderr)
        return 2

    if getattr(args, "json", False):
        print(json.dumps(doc, indent=2, ensure_ascii=False))
    elif action in ("install", "uninstall"):
        _render_install_doc(doc)
    else:
        _render_check_doc(doc)

    if action in ("install", "uninstall"):
        return 0 if doc.get("status") in ("completed", "planned") else 1
    if action == "doctor":
        return 0 if doc.get("status") == "healthy" else 1
    return 0


def guided_install_available() -> bool:
    """Bare `install` may route to the wizard only on a real TTY."""
    return _tty()
