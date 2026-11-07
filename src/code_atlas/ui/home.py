"""Bare-command home for Code Atlas."""

from __future__ import annotations

from code_atlas.ui.kit import NonInteractive, UIContext
from code_atlas.ui.tui import run_tui


def run_home(*, ctx: UIContext | None = None) -> int:
    if ctx is not None and not ctx.interactive:
        raise NonInteractive("Code Atlas home requires a TTY")
    return run_tui(ctx=ctx)


__all__ = ["run_home"]
