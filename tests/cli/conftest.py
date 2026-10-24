"""Fixtures de teste do CLI: ambiente XDG isolado por teste."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_repo"


@pytest.fixture()
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isola config/data dirs para não tocar o workspace real do usuário."""
    cfg = tmp_path / "xdg-config"
    data = tmp_path / "xdg-data"
    monkeypatch.setenv("CODE_ATLAS_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CODE_ATLAS_DATA_DIR", str(data))
    return tmp_path


@pytest.fixture()
def sample(tmp_path: Path) -> Path:
    """Cópia do fixture commitado + arquivos ignorados/segredo criados em runtime."""
    dest = tmp_path / "sample_repo"
    shutil.copytree(FIXTURE, dest)
    (dest / "ignored_dir").mkdir()
    (dest / "ignored_dir" / "skip.txt").write_text("ignored\n")
    (dest / "debug.tmp").write_text("tmp\n")  # casa padrão *.tmp do .gitignore
    return dest
