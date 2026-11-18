"""Workspace member discovery — multi-repo adoption (install-2.1).

Semantics equivalent to the-forge's vendored
``_installkit.discover_projects`` (deliberately NOT a byte-parity vendor
— see ``docs/install.md`` §installer divergence): a directory counts as
a workspace member when it carries a ``MEMBER_MARKERS`` entry — ``.git``
or a project manifest, so **gitless members are first-class**. The walk
never follows symlinks or junctions, skips hidden and generated
directories, caps at ``max_depth`` levels and never descends into a
discovered member (its inner modules stay isolated — a nested repo is
not absorbed into the parent's membership).

Discovery only *finds* members — it indexes nothing. Indexing stays an
explicit per-member ``code-atlas index`` action.
"""

from __future__ import annotations

import os
from pathlib import Path

# Generated/irrelevant directories the walk never descends into — a
# subset of ``ATLAS_DEFAULT_IGNORES`` (VCS internals + build caches).
IGNORED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".tox",
        ".nox",
        "dist",
        "build",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".idea",
        ".next",
        ".nuxt",
        "target",
        ".factory-cache",
    }
)

# A directory counts as a workspace member when it carries any of these —
# ``.git`` or a project manifest: gitless directories are first-class.
MEMBER_MARKERS = frozenset(
    {
        ".git",
        "forge.json",
        "forge.agentic.json",
        "pyproject.toml",
        "package.json",
    }
)

# ``.<state>/receipts`` convention the-forge scans on the install target
# (``_receipt_dirs``) — receipts co-located with the root they describe.
STATE_DIR_NAME = ".code-atlas"
RECEIPTS_DIRNAME = "receipts"

DEFAULT_MAX_DEPTH = 2


def _is_project_root(d: Path) -> bool:
    return any(os.path.lexists(d / marker) for marker in MEMBER_MARKERS)


def _is_plain_dir(entry: os.DirEntry[str]) -> bool:
    """A real directory: never a symlink or a junction (not followed)."""
    try:
        if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
            return False
    except OSError:
        return False
    isjunction = getattr(os.path, "isjunction", None)
    return not (isjunction is not None and isjunction(entry.path))


def discover_projects(root: Path, *, max_depth: int = DEFAULT_MAX_DEPTH) -> list[Path]:
    """Independent projects under a workspace root.

    ``root`` itself is included when it carries a marker; nested members
    are found up to ``max_depth`` levels deep. The walk never follows
    symlinks or junctions (external links cannot pull the discovery
    outside the root), skips hidden and ``IGNORED_DIRS`` directories,
    and never descends into a discovered member — child repositories
    keep their own isolated membership.
    """
    root = Path(root).resolve()
    found: list[Path] = []
    if _is_project_root(root):
        found.append(root)

    def _walk(d: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            with os.scandir(d) as it:
                children = sorted(
                    (entry for entry in it if _is_plain_dir(entry)),
                    key=lambda entry: entry.name,
                )
        except OSError:
            return
        for entry in children:
            name = entry.name
            if name.startswith(".") or name in IGNORED_DIRS:
                continue
            child = Path(entry.path)
            if _is_project_root(child):
                found.append(child.resolve())
            else:
                _walk(child, depth + 1)

    _walk(root, 1)
    return found


def state_dir(target: Path) -> Path:
    """``<target>/.code-atlas`` — the only path install writes under a
    target root (receipts + the dir's own ``.gitignore``). Service state
    (``atlas.db``, index) never lands here — it lives in the XDG data
    dir outside every repo root."""
    return Path(target) / STATE_DIR_NAME


def receipts_dir(target: Path) -> Path:
    """``<target>/.code-atlas/receipts`` — where the-forge's delegation
    scanner looks for ``forge/InstallReceipt/v1`` files."""
    return state_dir(target) / RECEIPTS_DIRNAME
