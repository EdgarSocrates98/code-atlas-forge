"""lf-0148 (cycle-8.1 Phase 3) — o wheel empacota o Explorer compilado e
``code-atlas explorer`` o serve numa instalação limpa.

Pesado mas honesto: copia a árvore de empacotamento para um tmpdir,
estágia ``code_atlas/explorer_dist/`` a partir de ``explorer/dist``,
builda o wheel com ``python -m build``, instala numa venv limpa com
``--no-deps`` (a superfície ``explorer`` é stdlib-only — tree-sitter só
entra no ``index``) e faz smoke HTTP real contra o servidor: ``/``,
rotas SPA (``/graph``, ``/domains``), um asset hashed e
``/api/v1/health``.

Skips explícitos (NOT RUN, nunca silenciosos): dist do Angular ausente
(rode ``npm --prefix explorer run build`` ou
``scripts/build_release.sh``), módulo ``build`` ausente no interpretador
do teste, ou o bootstrap isolado do build sem rede para baixar
``setuptools>=77``.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.packaging

ROOT = Path(__file__).resolve().parents[2]
ANGULAR_DIST = ROOT / "explorer" / "dist" / "explorer" / "browser"
INDEX_MARKER = "Code Atlas Explorer"  # <title> do index.html do Angular

# Heurística de "sem rede": o isolated env do `python -m build` faz
# `pip install setuptools>=77` — sem índice alcançável a falha é
# ambiental (NOT RUN), não quebra de contrato do pacote.
_NET_MARKERS = (
    "could not find a version", "no matching distribution",
    "temporary failure", "name or service not known", "connection refused",
    "network is unreachable", "timed out", "failed to establish",
)


def _copy_packaging_tree(dst: Path) -> None:
    """Copia o mínimo que ``python -m build`` precisa (pyproject, src,
    readme/license) — o repo inteiro tem node_modules/.venv e não pode
    ser duplicado, e buildar in-place sujaria a working tree."""
    dst.mkdir(parents=True)
    for name in ("pyproject.toml", "README.md", "LICENSE", "NOTICE"):
        src = ROOT / name
        if src.is_file():
            shutil.copy2(src, dst / name)
    shutil.copytree(
        ROOT / "src" / "code_atlas", dst / "src" / "code_atlas",
        ignore=shutil.ignore_patterns("__pycache__", "explorer_dist"))
    staged = dst / "src" / "code_atlas" / "explorer_dist"
    shutil.copytree(ANGULAR_DIST, staged)


def _build_wheel(srcdir: Path, outdir: Path) -> Path:
    proc = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir",
         str(outdir), str(srcdir)],
        capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        tail = (proc.stdout + proc.stderr)[-3000:]
        if any(m in tail.lower() for m in _NET_MARKERS):
            pytest.skip("NOT RUN: `python -m build` sem rede p/ baixar "
                        f"setuptools no isolated env — {tail[-400:]}")
        pytest.fail(f"`python -m build --wheel` falhou:\n{tail}")
    wheels = list(outdir.glob("*.whl"))
    assert len(wheels) == 1, f"esperava 1 wheel, achou {wheels}"
    return wheels[0]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _get(url: str, timeout: float = 10.0) -> tuple[int, bytes, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read(), r.headers.get_content_type()
    except urllib.error.HTTPError as e:
        return e.code, e.read(), ""


@pytest.fixture(scope="module")
def packaged_install(tmp_path_factory: pytest.TempPathFactory):
    """Wheel com explorer_dist → venv limpa → `code-atlas explorer`
    servindo. Produz ``(base_url, venv_python, wheel_path)``."""
    import importlib.util

    if not (ANGULAR_DIST / "index.html").is_file():
        pytest.skip("NOT RUN: explorer/dist ausente — rode "
                    "`npm --prefix explorer run build` ou "
                    "`scripts/build_release.sh` antes desta suíte")
    if importlib.util.find_spec("build") is None:
        pytest.skip("NOT RUN: módulo `build` ausente no interpretador "
                    "do teste (instale o extra `dev`)")

    work = tmp_path_factory.mktemp("wheel-explorer")
    pkg_src = work / "pkgsrc"
    _copy_packaging_tree(pkg_src)
    wheel = _build_wheel(pkg_src, work / "out")

    venv = work / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)],
                   check=True, timeout=120)
    # POSIX: bin/python; Windows: Scripts/python.exe
    vpy = next(
        (p for p in (venv / "bin" / "python", venv / "bin" / "python.exe",
                     venv / "Scripts" / "python.exe") if p.is_file()),
        None)
    assert vpy is not None, "venv criada sem python executável"
    subprocess.run(
        [str(vpy), "-m", "pip", "install", "--quiet", "--no-deps",
         str(wheel)], check=True, timeout=300)

    data_dir = work / "data"
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "PYTHONHOME")}
    env["CODE_ATLAS_DATA_DIR"] = str(data_dir)
    env["CODE_ATLAS_CONFIG_DIR"] = str(work / "conf")
    subprocess.run(
        [str(vpy), "-c",
         "from pathlib import Path\n"
         "from code_atlas.storage import connect, migrate\n"
         f"conn = connect(Path({str(data_dir / 'atlas.db')!r}))\n"
         "migrate(conn)\nconn.close()\n"],
        check=True, env=env, timeout=60)

    port = _free_port()
    cli = next(
        (p for p in (venv / "bin" / "code-atlas",
                     venv / "Scripts" / "code-atlas.exe") if p.is_file()),
        None)
    assert cli is not None, "wheel não instalou o entrypoint code-atlas"
    proc = subprocess.Popen(
        [str(cli), "explorer", "--port", str(port)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                out = proc.stdout.read() if proc.stdout else ""
                pytest.fail(f"`code-atlas explorer` morreu no boot "
                            f"(rc={proc.returncode}):\n{out[-2000:]}")
            try:
                status, _, _ = _get(f"{base}/api/v1/health", timeout=1)
            except OSError:
                status = 0
            if status == 200:
                break
            time.sleep(0.2)
        else:
            pytest.fail("`code-atlas explorer` não respondeu "
                        "/api/v1/health em 30s")
        yield base, vpy, wheel
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def test_wheel_contains_explorer_assets(packaged_install) -> None:
    """O wheel carrega index.html + assets hashed em
    ``code_atlas/explorer_dist/``."""
    _, _, wheel = packaged_install
    names = zipfile.ZipFile(wheel).namelist()
    assets = [n for n in names if n.startswith("code_atlas/explorer_dist/")]
    assert "code_atlas/explorer_dist/index.html" in names
    assert any(a.endswith(".js") for a in assets), "sem JS hashed no wheel"
    assert any(a.endswith(".css") for a in assets), "sem CSS hashed no wheel"
    source = {f.name for f in ANGULAR_DIST.iterdir() if f.is_file()}
    staged = {Path(a).name for a in assets}
    assert source <= staged, (
        f"wheel perdeu assets do dist: {sorted(source - staged)}")


def test_clean_venv_explorer_serves(packaged_install) -> None:
    """Instalação limpa: rotas SPA caem no index.html, asset hashed e
    /api/v1/health respondem 200."""
    base, _, _ = packaged_install

    for route in ("/", "/graph", "/domains"):
        status, body, ctype = _get(f"{base}{route}")
        assert status == 200, f"{route} -> {status}"
        assert ctype == "text/html", f"{route} content-type {ctype}"
        assert INDEX_MARKER.encode() in body, f"{route} sem index.html"

    hashed_js = sorted(p.name for p in ANGULAR_DIST.glob("*.js"))
    assert hashed_js, "dist sem chunks JS para o smoke"
    status, body, ctype = _get(f"{base}/{hashed_js[0]}")
    assert status == 200, f"/{hashed_js[0]} -> {status}"
    assert body == (ANGULAR_DIST / hashed_js[0]).read_bytes(), (
        "asset servido difere do dist — conteúdo corrompido")

    status, body, ctype = _get(f"{base}/api/v1/health")
    assert status == 200, f"/api/v1/health -> {status}"
    payload = json.loads(body)
    assert payload["status"] == "ok"
    assert ctype == "application/json"
