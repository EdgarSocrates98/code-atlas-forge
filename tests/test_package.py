"""Contrato básico do pacote: versão coerente e layout src."""

from __future__ import annotations

import json
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


def test_capability_manifests_match_and_are_packaged() -> None:
    root_manifests = [ROOT / "forge.json", ROOT / "forge.agentic.json"]
    package_manifests = [ROOT / "src" / "code_atlas" / "forge" / p.name for p in root_manifests]
    for root_path, package_path in zip(root_manifests, package_manifests, strict=True):
        assert root_path.exists(), f"missing source manifest: {root_path.name}"
        assert package_path.exists(), f"missing package manifest: {package_path.name}"
        assert json.loads(root_path.read_text(encoding="utf-8")) == json.loads(
            package_path.read_text(encoding="utf-8")
        )
    assert (
        json.loads(root_manifests[0].read_text(encoding="utf-8"))["forge_id"]
        == "code-atlas-forge"
    )
    assert json.loads(root_manifests[1].read_text(encoding="utf-8"))["version"] == __version__


def test_agent_roster_matches_manifest_and_is_packaged() -> None:
    manifest = json.loads((ROOT / "forge.agentic.json").read_text(encoding="utf-8"))
    roster_dir = ROOT / manifest["agent_roster_paths"][0]
    roster_ids = sorted(path.stem for path in roster_dir.glob("*.md"))
    assert roster_ids == sorted(manifest["agents"])
    for agent_id in roster_ids:
        agent_path = (
            ROOT
            / "src"
            / "code_atlas"
            / "installation"
            / "assets"
            / "agents"
            / f"{agent_id}.md"
        )
        assert agent_path.exists()


def test_runtime_dependencies_are_pinned_tree_sitter() -> None:
    """Cycle 2: deps de runtime = apenas a pilha Tree-sitter aprovada (ADR)."""
    raw = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = raw["project"].get("dependencies", [])
    allowed = {"tree-sitter", "tree-sitter-python", "tree-sitter-typescript"}
    assert deps, "Cycle 2 exige tree-sitter real — deps não podem ser vazias"
    for dep in deps:
        name = dep.split("==")[0].split(">=")[0].split("[")[0]
        assert name in allowed, f"dependência não aprovada: {dep}"
        assert "==" in dep, f"pin exato obrigatório: {dep}"
