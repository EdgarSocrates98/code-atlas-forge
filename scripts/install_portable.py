"""Install Code Atlas Forge into a user-scoped, portable virtualenv.

This bootstrap deliberately has no dependency on The Forge or on a checkout
being present after installation.  It is the small cross-platform entrypoint
used by install.sh, install.windows.sh and install.ps1.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

FORGE_ID = "code-atlas-forge"
CLI = "code-atlas"
MIN_PYTHON = (3, 12)


def _root() -> Path:
    return Path(__file__).resolve().parent.parent


def _install_home() -> Path:
    override = os.environ.get("FORGE_INSTALL_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".forjas" / FORGE_ID).resolve()


def _venv_path(home: Path) -> Path:
    return home / "venv"


def _python_executable(home: Path) -> Path:
    return _venv_path(home) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _cli_executable(home: Path) -> Path:
    return _venv_path(home) / ("Scripts/code-atlas.exe" if os.name == "nt" else "bin/code-atlas")


def _launcher_path(home: Path) -> Path:
    return home / "bin" / (f"{CLI}.cmd" if os.name == "nt" else CLI)


def _run(command: list[str], *, cwd: Path) -> None:
    print("$ " + " ".join(shlex.quote(part) for part in command))
    subprocess.run(command, cwd=cwd, check=True)


def _write_launcher(home: Path) -> Path:
    launcher = _launcher_path(home)
    launcher.parent.mkdir(parents=True, exist_ok=True)
    target = _cli_executable(home)
    if os.name == "nt":
        launcher.write_text(f'@echo off\r\n"{target}" %*\r\n', encoding="utf-8")
    else:
        launcher.write_text(
            "#!/usr/bin/env sh\n"
            f'exec {shlex.quote(str(target))} "$@"\n',
            encoding="utf-8",
        )
        launcher.chmod(0o755)
    return launcher


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Portable Code Atlas Forge installer")
    parser.add_argument("--dry-run", action="store_true", help="show the plan without writing")
    parser.add_argument("--editable", action="store_true", help="install this checkout editable")
    parser.add_argument(
        "--python", dest="python_exe", help="Python executable used to create the venv"
    )
    args = parser.parse_args(argv)

    if sys.version_info < MIN_PYTHON:
        print(f"Code Atlas Forge requires Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+", file=sys.stderr)
        return 2

    root = _root()
    home = _install_home()
    venv_python = _python_executable(home)
    launcher = _launcher_path(home)
    source_python = Path(args.python_exe).expanduser() if args.python_exe else Path(sys.executable)
    mode = "editable" if args.editable else "wheel"

    print(f"Code Atlas Forge - portable install - {mode}")
    print(f"source:   {root}")
    print(f"venv:     {_venv_path(home)}")
    print(f"launcher: {launcher}")
    if args.dry_run:
        print("plan only: no files, environments or PATH entries were changed")
        return 0

    home.mkdir(parents=True, exist_ok=True)
    if not venv_python.exists():
        _run([str(source_python), "-m", "venv", str(_venv_path(home))], cwd=root)
    install_args = [
        str(venv_python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
    ]
    install_args.append("-e" if args.editable else ".")
    _run(install_args, cwd=root)
    launcher = _write_launcher(home)
    receipt = {
        "schema": "forge-portable-install/1",
        "forge_id": FORGE_ID,
        "cli": CLI,
        "status": "installed",
        "mode": mode,
        "source": str(root),
        "venv": str(_venv_path(home)),
        "launcher": str(launcher),
        "python": str(venv_python),
        "installed_at": datetime.now(UTC).isoformat(),
    }
    (home / "install.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(f"installed: {launcher}")
    print(f"add to PATH: {launcher.parent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
