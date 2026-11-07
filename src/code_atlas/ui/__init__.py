"""Forge UI Kit — stdlib-only interactive terminal layer (vendored from
spark-forge-aws; byte-adapted for code_atlas imports).

Zero dependencies, cross-platform (msvcrt on Windows, termios on
POSIX). Every interaction has a headless equivalent so automation never
blocks on a TTY that isn't there.
"""

from code_atlas.ui.i18n import t
from code_atlas.ui.kit import (
    UIContext,
    confirm,
    dashboard,
    multi_select,
    prompt,
    select,
    status_table,
)
from code_atlas.ui.home import run_home
from code_atlas.ui.tui import run_tui

__all__ = [
    "UIContext",
    "confirm",
    "dashboard",
    "multi_select",
    "prompt",
    "select",
    "status_table",
    "run_home",
    "run_tui",
    "t",
]
