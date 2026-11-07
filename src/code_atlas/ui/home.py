"""Bare-command home for Code Atlas."""

from __future__ import annotations

from code_atlas.ui.kit import UIContext
from code_atlas.ui.tui import run_tui


def run_home(*, ctx: UIContext | None = None) -> int:
    return run_tui(ctx=ctx)


__all__ = ["run_home"]
