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


def test_portable_installer_plan_includes_federation_registration() -> None:
    """The install plan must register into ~/.forge/installations — otherwise
    `theforge setup` cannot discover or delegate to the atlas (wave-8 gap)."""
    env = {**os.environ, "FORGE_INSTALL_HOME": "/nonexistent"}
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", "--json"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    import json

    plan = json.loads(result.stdout)
    stages = [s["stage"] for s in plan["stages"]]
    assert "register install" in stages
    assert stages[-1] == "register install"  # registered only after CLI verified


def test_register_writes_installation_manifest(tmp_path: Path, monkeypatch) -> None:
    import importlib.util
    import json

    spec = importlib.util.spec_from_file_location("install_portable", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    monkeypatch.setenv("FORGE_HOME_OVERRIDE", str(tmp_path))
    home = tmp_path / "install-home"
    home.mkdir()
    (home / "venv" / "bin").mkdir(parents=True)
    launcher = home / "bin" / "code-atlas"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.touch()

    path = mod._register(home, launcher, Path(sys.executable), "wheel")

    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["schema"] == "forge/InstallationManifest/v1"
    assert doc["forge_id"] == "code-atlas-forge"
    assert doc["installation_kind"] == "service"
    assert doc["version"] != "0.0.0"
    assert doc["cli"]["name"] == "code-atlas"
    assert doc["cli"]["shim"] == str(launcher)
    assert doc["hosts"] == {}
    assert set(doc["install"]["operations"]) >= {"plan", "apply", "status", "doctor"}
    assert doc["mcp_verify_tool"] == "atlas_status"


def test_editable_install_passes_project_path_to_pip(tmp_path: Path, monkeypatch) -> None:
    """Regression: `--editable` must emit `pip install -e .` — a bare `-e`
    makes pip exit 2 ('-e option requires 1 argument')."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("install_portable", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    calls: list[list[str]] = []

    def fake_run(cmd, **_kw):
        calls.append(list(cmd))

    home = tmp_path / "install"
    venv_bin = home / "venv" / ("Scripts" if os.name == "nt" else "bin")
    venv_bin.mkdir(parents=True)
    (venv_bin / ("python.exe" if os.name == "nt" else "python")).touch()
    (venv_bin / ("code-atlas.exe" if os.name == "nt" else "code-atlas")).touch()

    monkeypatch.setattr(mod, "_run", fake_run)
    monkeypatch.setattr(mod, "_write_launcher", lambda _h: tmp_path / "bin" / "code-atlas")
    monkeypatch.setattr(mod, "_register", lambda *a, **kw: tmp_path / "reg.json")
    monkeypatch.setenv("FORGE_INSTALL_HOME", str(home))
    monkeypatch.setenv("FORGE_HOME_OVERRIDE", str(tmp_path / "forgehome"))

    rc = mod.main(["--editable", "--quiet", "--json"])
    assert rc == 0
    pip = next(c for c in calls if "pip" in c and "install" in c)
    assert "-e" in pip
    assert pip.index("-e") + 1 < len(pip) and pip[pip.index("-e") + 1] == "."
