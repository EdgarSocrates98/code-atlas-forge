"""Contrato básico do pacote: versão coerente e layout src."""

from __future__ import annotations

import tomllib
from pathlib import Path

from code_atlas import __version__

ROOT = Path(__file__).resolve().parent.parent


def test_version_matches_pyproject() -> None:
    # pyproject usa dynamic version via attr code_atlas.__version__ — fonte única.
    raw = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert raw["tool"]["setuptools"]["dynamic"]["version"]["attr"] == "code_atlas.__version__"
    assert __version__


def test_src_layout() -> None:
    pkg = ROOT / "src" / "code_atlas"
    assert pkg.is_dir()
    assert (pkg / "__init__.py").exists()
    for sub in ("contracts", "discovery", "storage", "indexing", "observability"):
        assert (pkg / sub / "__init__.py").exists(), f"missing {sub}"


def test_no_runtime_dependencies() -> None:
    raw = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert raw["project"].get("dependencies", []) == []
