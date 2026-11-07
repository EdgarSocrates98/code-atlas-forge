from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install_portable.py"


def test_portable_installer_dry_run_is_side_effect_free(tmp_path: Path) -> None:
    env = {**os.environ, "FORGE_INSTALL_HOME": str(tmp_path / "install")}
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "plan only" in result.stdout
    assert not (tmp_path / "install").exists()


def test_portable_install_assets_are_present() -> None:
    assert (ROOT / "install.sh").exists()
    assert (ROOT / "install.windows.sh").exists()
    assert (ROOT / "install.ps1").exists()
