"""Install Code Atlas Forge into a user-scoped, portable virtualenv.

This bootstrap deliberately has no dependency on The Forge or on a checkout
being present after installation.  It is the small cross-platform entrypoint
used by install.sh, install.windows.sh and install.ps1.

Channels: ``--dry-run`` emits a JSON plan on stdout and a real install emits
the ``forge-portable-install/1`` receipt on stdout (the same document that
lands in ``<install-home>/install.json``).  All human-facing presentation —
banner, numbered stepper with spinner, panels, typed FORGE-* refusals —
goes to stderr, honoring NO_COLOR / FORGE_NO_UI / CI / GITHUB_ACTIONS /
TERM=dumb and the --quiet/--plain/--json flags.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

FORGE_ID = "code-atlas-forge"
NAME = "Code Atlas Forge"
CLI = "code-atlas"
MIN_PYTHON = (3, 12)
RECEIPT_SCHEMA = "forge-portable-install/1"
PLAN_SCHEMA = "forge-portable-install-plan/1"

_REMEDIES = {
    "FORGE-PYTHON-INCOMPATIBLE": (
        f"install Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ and reopen the shell, "
        "or pass --python <exe>"
    ),
    "FORGE-PYTHON-UNEXECUTABLE": "fix --python — the interpreter must execute `-m venv`",
    "FORGE-INSTALL-VENV-FAILED": "remove the half-created venv under the install home and re-run",
    "FORGE-INSTALL-RUNTIME-FAILED": "see the pip output above — the step log is the evidence",
    "FORGE-INSTALL-CLI-MISSING": (
        "the package installed but exposes no console script — check pyproject [project.scripts]"
    ),
}

_STEPS = ["create venv", "install runtime", "write launcher", "write receipt", "verify cli"]


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
    return _venv_path(home) / (f"Scripts/{CLI}.exe" if os.name == "nt" else f"bin/{CLI}")


def _launcher_path(home: Path) -> Path:
    return home / "bin" / (f"{CLI}.cmd" if os.name == "nt" else CLI)


def _enable_windows_vt() -> None:
    """Best-effort ENABLE_VIRTUAL_TERMINAL_PROCESSING for legacy conhost."""
    if os.name != "nt":
        return
    try:
        import ctypes

        k32 = ctypes.windll.kernel32
        for fd in (-11, -12):
            handle = k32.GetStdHandle(fd)
            mode = ctypes.c_ulong()
            if k32.GetConsoleMode(handle, ctypes.byref(mode)):
                k32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:  # pragma: no cover — best effort on exotic hosts
        pass


class _Ux:
    """Human-facing presentation on stderr; stdout stays machine-clean."""

    _SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    _SPINNER_ASCII = "|/-\\"

    def __init__(self, enabled: bool):
        self.enabled = enabled
        self._stop = threading.Event()
        self._spin_thread: threading.Thread | None = None
        self._t0 = 0.0
        enc = (getattr(sys.stderr, "encoding", None) or "").lower()
        uni = "utf" in enc or os.environ.get("WT_SESSION") or os.environ.get("MSYSTEM")
        self._g = (
            {
                "ok": "✓", "fail": "✗", "plan": "◌", "em": "◆",
                "arrow": "→", "rule": "─", "bullet": "·", "spin": self._SPINNER,
            }
            if uni
            else {
                "ok": "OK", "fail": "x", "plan": "o", "em": "*",
                "arrow": "->", "rule": "-", "bullet": "-", "spin": self._SPINNER_ASCII,
            }
        )
        if enabled:
            _enable_windows_vt()

    def _c(self, code: str, text: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m"

    def _w(self, text: str = "") -> None:
        sys.stderr.write(text + "\n")
        sys.stderr.flush()

    def _spin(self, i: int, n: int, label: str) -> None:
        g, c, k = self._g, self._c, 0
        while not self._stop.wait(0.09):
            frame = g["spin"][k % len(g["spin"])]
            dt = time.monotonic() - self._t0
            line = f"  {c('38;5;208', frame)} {c('2', f'[{i}/{n}]')} {label} "
            sys.stderr.write(f"\r\x1b[K{line}{c('2', f'{dt:4.1f}s')}")
            sys.stderr.flush()
            k += 1

    def banner(self, mode: str, dry_run: bool) -> None:
        if not self.enabled:
            return
        g, c = self._g, self._c
        scope = "plan only" if dry_run else "user scope"
        head = f"portable install {g['bullet']} {mode} {g['bullet']} {scope}"
        self._w("")
        self._w(f"  {c('38;5;208;1', g['em'] + ' FORJAS')} {c('2', g['bullet'])} {c('1', NAME)}")
        self._w(f"  {c('2', head)}")
        self._w(f"  {c('2', g['rule'] * 54)}")

    def step_planned(self, i: int, n: int, label: str) -> None:
        if self.enabled:
            g, c = self._g, self._c
            self._w(f"  {c('33', g['plan'])} {c('2', f'[{i}/{n}]')} {label} {c('2', 'planned')}")

    def step_start(self, i: int, n: int, label: str) -> None:
        if not self.enabled:
            return
        self._t0 = time.monotonic()
        self._stop.clear()
        self._spin_thread = threading.Thread(target=self._spin, args=(i, n, label), daemon=True)
        self._spin_thread.start()

    def step_done(self, i: int, n: int, label: str, *, ok: bool, note: str = "") -> None:
        self._stop.set()
        if self._spin_thread is not None:
            self._spin_thread.join(timeout=0.5)
            self._spin_thread = None
        if not self.enabled:
            return
        g, c = self._g, self._c
        dt = time.monotonic() - self._t0
        mark = c("32", g["ok"]) if ok else c("31", g["fail"])
        self._w(f"\r\x1b[K  {mark} {c('2', f'[{i}/{n}]')} {label} {c('2', f'{dt:5.1f}s')}")
        if note:
            self._w(f"      {c('2', note)}")

    def summary(self, rows: list[tuple[str, str]], hint: str | None, *, dry_run: bool) -> None:
        if not self.enabled:
            return
        g, c = self._g, self._c
        self._w(f"  {c('2', g['rule'] * 54)}")
        if dry_run:
            tail = g["rule"] + " nothing was written"
            self._w(f"  {c('33', g['plan'])} {c('1', 'install plan')} {c('2', tail)}")
        else:
            self._w(f"  {c('32', g['ok'])} {c('1', 'install complete')}")
        self._w("")
        for key, value in rows:
            self._w(f"    {c('36', key.ljust(9))} {value}")
        if hint:
            self._w("")
            self._w(f"    {c('33', 'next')}  {hint}")
        self._w(f"  {c('2', g['rule'] * 54)}")
        self._w("")

    def refuse(self, kind: str, detail: str) -> None:
        # The FORGE-* line is the greppable contract — always emitted.
        if not self.enabled:
            self._w(f"{kind}: {detail}")
            return
        g, c = self._g, self._c
        self._w("")
        head = f"{c('31', g['fail'])} {c('1;31', 'install refused')} "
        self._w(f"  {head}{c('2', g['rule'])} {c('31', kind)}")
        self._w(f"    {c('2', detail)}")
        remedy = _REMEDIES.get(kind)
        if remedy:
            self._w(f"    {c('33', 'remedy')}  {remedy}")
        self._w("")

    def close(self) -> None:
        self._stop.set()
        if self._spin_thread is not None:
            self._spin_thread.join(timeout=0.5)
            self._spin_thread = None


def _ui_enabled(args: argparse.Namespace) -> bool:
    if args.quiet or args.plain:
        return False
    forced = os.environ.get("FORGE_BOOTSTRAP_UI", "").strip().lower()
    if forced in ("always", "on", "rich", "1", "yes", "true"):
        return True
    if forced in ("never", "off", "plain", "0", "no", "false"):
        return False
    if os.environ.get("NO_COLOR") is not None or os.environ.get("FORGE_NO_UI"):
        return False
    if (
        os.environ.get("TERM", "") == "dumb"
        or os.environ.get("CI")
        or os.environ.get("GITHUB_ACTIONS")
    ):
        return False
    return sys.stderr.isatty()


def _run(command: list[str], *, cwd: Path, quiet: bool) -> None:
    if not quiet:
        shown = " ".join(shlex.quote(p) for p in command)
        sys.stderr.write(f"      \x1b[2m$ {shown}\x1b[0m\n")
        sys.stderr.flush()
    subprocess.run(command, cwd=cwd, check=True)


def _write_launcher(home: Path) -> Path:
    launcher = _launcher_path(home)
    launcher.parent.mkdir(parents=True, exist_ok=True)
    target = _cli_executable(home)
    if os.name == "nt":
        launcher.write_text(f'@echo off\r\n"{target}" %*\r\n', encoding="utf-8")
    else:
        launcher.write_text(
            f'#!/usr/bin/env sh\nexec {shlex.quote(str(target))} "$@"\n',
            encoding="utf-8",
        )
        launcher.chmod(0o755)
    return launcher


def _emit_json(doc: dict) -> None:
    print(json.dumps(doc, indent=2))


def _install(args: argparse.Namespace, ui: _Ux, refuse, mode: str) -> int:
    root = _root()
    home = _install_home()
    venv_python = _python_executable(home)
    source_python = Path(args.python_exe).expanduser() if args.python_exe else Path(sys.executable)
    n = len(_STEPS)

    ui.step_start(1, n, _STEPS[0])
    try:
        home.mkdir(parents=True, exist_ok=True)
        if not venv_python.exists():
            cmd = [str(source_python), "-m", "venv", str(_venv_path(home))]
            _run(cmd, cwd=root, quiet=args.quiet)
        ui.step_done(1, n, _STEPS[0], ok=True, note=str(_venv_path(home)))
    except Exception as exc:
        ui.step_done(1, n, _STEPS[0], ok=False)
        return refuse("FORGE-INSTALL-VENV-FAILED", str(exc))

    ui.step_start(2, n, _STEPS[1])
    try:
        install_args = [
            str(venv_python), "-m", "pip", "install",
            "--disable-pip-version-check", "--no-input",
            "-e" if args.editable else ".",
        ]
        _run(install_args, cwd=root, quiet=args.quiet)
        ui.step_done(2, n, _STEPS[1], ok=True, note=f"{mode} from {root.name}/")
    except subprocess.CalledProcessError as exc:
        ui.step_done(2, n, _STEPS[1], ok=False)
        code = exc.returncode or 1
        return refuse("FORGE-INSTALL-RUNTIME-FAILED", f"pip exited {exc.returncode}", code=code)

    ui.step_start(3, n, _STEPS[2])
    try:
        launcher = _write_launcher(home)
        ui.step_done(3, n, _STEPS[2], ok=True, note=str(launcher))
    except Exception as exc:
        ui.step_done(3, n, _STEPS[2], ok=False)
        return refuse("FORGE-INSTALL-RUNTIME-FAILED", f"launcher write failed: {exc}")

    receipt = {
        "schema": RECEIPT_SCHEMA,
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
    ui.step_start(4, n, _STEPS[3])
    try:
        (home / "install.json").write_text(
            json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
        )
        ui.step_done(4, n, _STEPS[3], ok=True, note=str(home / "install.json"))
    except Exception as exc:
        ui.step_done(4, n, _STEPS[3], ok=False)
        return refuse("FORGE-INSTALL-RUNTIME-FAILED", f"receipt write failed: {exc}")

    ui.step_start(5, n, _STEPS[4])
    if _cli_executable(home).exists():
        ui.step_done(5, n, _STEPS[4], ok=True, note=str(_cli_executable(home)))
    else:
        ui.step_done(5, n, _STEPS[4], ok=False)
        return refuse(
            "FORGE-INSTALL-CLI-MISSING",
            f"console script not found at {_cli_executable(home)}",
        )

    ui.summary(
        [
            ("cli", CLI),
            ("mode", mode),
            ("venv", str(_venv_path(home))),
            ("launcher", str(launcher)),
            ("receipt", str(home / "install.json")),
        ],
        f"add to PATH: {launcher.parent} — then run `{CLI} --help`",
        dry_run=False,
    )
    if args.json or not sys.stdout.isatty():
        _emit_json(receipt)
    return 0


def _plan(args: argparse.Namespace, ui: _Ux, mode: str) -> int:
    root = _root()
    home = _install_home()
    source_python = Path(args.python_exe).expanduser() if args.python_exe else Path(sys.executable)
    n = len(_STEPS)
    plan = {
        "schema": PLAN_SCHEMA,
        "forge_id": FORGE_ID,
        "cli": CLI,
        "mode": mode,
        "source": str(root),
        "install_home": str(home),
        "venv": str(_venv_path(home)),
        "launcher": str(_launcher_path(home)),
        "python": str(source_python),
        "mutates": False,
        "note": "plan only — no files, environments or PATH entries were changed",
        "stages": [{"stage": s, "status": "planned"} for s in _STEPS],
    }
    for i, label in enumerate(_STEPS, 1):
        ui.step_planned(i, n, label)
    ui.summary(
        [
            ("source", str(root)),
            ("home", str(home)),
            ("venv", str(_venv_path(home))),
            ("launcher", str(_launcher_path(home))),
        ],
        f"run without --dry-run to install {CLI}",
        dry_run=True,
    )
    if args.json or not sys.stdout.isatty():
        _emit_json(plan)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Portable {NAME} installer")
    parser.add_argument("--dry-run", action="store_true", help="show the plan without writing")
    parser.add_argument("--editable", action="store_true", help="install this checkout editable")
    parser.add_argument(
        "--python", dest="python_exe", help="Python executable used to create the venv"
    )
    parser.add_argument("--quiet", action="store_true", help="suppress human output entirely")
    parser.add_argument("--plain", action="store_true", help="disable decoration")
    parser.add_argument("--json", action="store_true", help="emit the JSON report on stdout")
    args = parser.parse_args(argv)

    ui = _Ux(_ui_enabled(args))
    mode = "editable" if args.editable else "wheel"

    def refuse(kind: str, detail: str, code: int = 2) -> int:
        if args.json or not sys.stdout.isatty():
            _emit_json({"status": "failed", "error": {"kind": kind, "detail": detail}})
        ui.refuse(kind, detail)
        return code

    try:
        if sys.version_info < MIN_PYTHON:
            return refuse(
                "FORGE-PYTHON-INCOMPATIBLE",
                f"{NAME} requires Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+; "
                f"found {sys.version.split()[0]}",
            )
        ui.banner(mode, args.dry_run)
        if args.dry_run:
            return _plan(args, ui, mode)
        return _install(args, ui, refuse, mode)
    finally:
        ui.close()


if __name__ == "__main__":
    raise SystemExit(main())
