"""Small terminal command centre for Code Atlas.

The TUI intentionally uses the existing zero-dependency UI kit: it is useful
on a real terminal, deterministic in tests and refuses cleanly when stdin or
stdout is not interactive.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from code_atlas.ui.kit import NonInteractive, UIContext, dashboard, select


def _print_doc(doc: Any) -> None:
    print(json.dumps(doc, indent=2, ensure_ascii=False, default=str))


def _status() -> None:
    from code_atlas.install import lifecycle

    _print_doc(lifecycle.status())


def _doctor() -> None:
    from code_atlas.install import lifecycle

    _print_doc(lifecycle.doctor(scope="project"))


def _install() -> int:
    from code_atlas.install.cli import run_guided_install

    return run_guided_install()


def entries() -> list[tuple[str, Callable[[], object]]]:
    return [("status", _status), ("doctor", _doctor), ("install wizard", _install)]


def run_tui(*, ctx: UIContext | None = None) -> int:
    """Run the compact Code Atlas command centre."""
    ctx = ctx or UIContext.detect()
    if not ctx.interactive:
        raise NonInteractive("Code Atlas TUI requires a TTY")
    actions = entries()
    while True:
        dashboard(
            "CODE ATLAS  command centre",
            [
                ("Workspace", [("root", str(Path.cwd()))]),
                ("Actions", [(label, "open") for label, _ in actions]),
            ],
            ctx=ctx,
        )
        idx = select(
            "What do you want to inspect?", [label for label, _ in actions] + ["quit"], ctx=ctx
        )
        if idx is None or idx == len(actions):
            return 0
        result = actions[idx][1]()
        if isinstance(result, int) and result != 0:
            return result
        print()


__all__ = ["entries", "run_tui"]
