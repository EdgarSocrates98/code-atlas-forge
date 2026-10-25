"""Hardening: confinamento de subprocess, traversal, DoS básico (lf-0005)."""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

from code_atlas import cli

SRC = Path(__file__).resolve().parents[2] / "src"
ALLOWED_SUBPROCESS = {"gitbin.py", "spawn.py"}  # spawn.py = resolvers/lsp


def test_subprocess_confined_to_gitbin() -> None:
    offenders: list[str] = []
    for py in SRC.rglob("*.py"):
        if py.name in ALLOWED_SUBPROCESS:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Import) and any(
                a.name.split(".")[0] == "subprocess" for a in node.names
            )) or (isinstance(node, ast.ImportFrom) and node.module == "subprocess"):
                offenders.append(f"{py.name}:{node.lineno}")
    assert offenders == []


def test_no_network_or_shell_exec_in_src() -> None:
    # lf-0086: src/code_atlas/api/ é a fronteira HTTP deliberada do
    # Explorer (loopback read-only) — única exceção permitida e auditada.
    api_boundary = (SRC / "code_atlas" / "api").resolve()
    banned = {"socket", "urllib", "requests", "http", "ftplib", "smtplib",
              "telnetlib", "asyncio", "pty", "eval"}
    offenders: list[str] = []
    for py in SRC.rglob("*.py"):
        if api_boundary in py.resolve().parents:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders.extend(
                    f"{py.name}:{a.name}" for a in node.names
                    if a.name.split(".")[0] in banned
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in banned:
                    offenders.append(f"{py.name}:{node.module}")
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ("eval", "exec", "compile")
            ):
                offenders.append(f"{py.name}:{node.lineno} {node.func.id}")
    assert offenders == []


def test_index_rejects_traversal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODE_ATLAS_CONFIG_DIR", str(tmp_path / "c"))
    monkeypatch.setenv("CODE_ATLAS_DATA_DIR", str(tmp_path / "d"))
    rc = cli.main(["index", "../.."])
    assert rc == 3


def test_index_rejects_filesystem_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODE_ATLAS_CONFIG_DIR", str(tmp_path / "c"))
    monkeypatch.setenv("CODE_ATLAS_DATA_DIR", str(tmp_path / "d"))
    rc = cli.main(["index", "/"])
    assert rc == 3


def test_db_path_confined_to_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # CODE_ATLAS_DATA_DIR malicioso tentando sair — resolve normal, mas o DB
    # sempre vive DENTRO do data_dir resolvido.
    monkeypatch.setenv("CODE_ATLAS_DATA_DIR", str(tmp_path / "d") + "/../d2")
    db = cli.db_path().resolve()
    assert db.parent.name in ("d", "d2")
    assert db.name == "atlas.db"


def test_zero_byte_and_oversize_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODE_ATLAS_CONFIG_DIR", str(tmp_path / "c"))
    monkeypatch.setenv("CODE_ATLAS_DATA_DIR", str(tmp_path / "d"))
    tgt = tmp_path / "tgt"
    tgt.mkdir()
    (tgt / "empty.py").write_bytes(b"")
    (tgt / "big.bin").write_bytes(b"x" * (2 * 1024 * 1024))
    from code_atlas.discovery import ScanOptions, scan

    res = scan(tgt, ScanOptions(max_file_size=1024))
    by = {f.path: f for f in res.files}
    assert by["empty.py"].sha256 is not None  # sha256 de b"" é válido
    assert by["big.bin"].sha256 is None


@pytest.mark.skipif(sys.platform == "win32",
                    reason="os.symlink exige privilégio no Windows")
def test_symlink_to_etc_warns_not_follows(tmp_path: Path) -> None:
    if not Path("/etc").exists():
        pytest.skip("/etc absent")
    (tmp_path / "x.py").write_text("x=1\n")
    os.symlink("/etc", tmp_path / "etc_link")
    from code_atlas.discovery import WarningKind, scan

    res = scan(tmp_path)
    assert not any("etc_link/" in f.path for f in res.files)
    assert any(w.kind == WarningKind.SYMLINK_ESCAPE for w in res.warnings)


def test_pathological_depth_capped(tmp_path: Path) -> None:
    deep = tmp_path
    for i in range(60):
        deep = deep / f"d{i}"
    deep.mkdir(parents=True)
    (deep / "leaf.txt").write_text("x\n")
    from code_atlas.discovery import ScanOptions, scan

    res = scan(tmp_path, ScanOptions(max_depth=20))
    assert "leaf.txt" not in {f.path.split("/")[-1] for f in res.files}


# --- lf-0024 Phase 10: security/portability sobre resolvers/ ----------


def test_lsp_spawn_env_redacts_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env leak-minimizado: variáveis de segredo NUNCA passam ao server."""
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
    monkeypatch.setenv("GITHUB_TOKEN", "y")
    monkeypatch.setenv("CODE_ATLAS_SECRET", "z")
    monkeypatch.setenv("MY_API_KEY", "k")
    from code_atlas.resolvers.lsp.spawn import _leak_env

    env = _leak_env()
    for k in env:
        assert not any(
            s in k
            for s in ("SECRET", "TOKEN", "KEY", "PASS", "CRED", "AUTH")
        ), k


def test_no_auto_download_or_install_in_src() -> None:
    """Nenhum caminho baixa/instala nada — nem LSP server, nem SCIP
    indexer, nem dependências."""
    banned_tokens = ("download", "pip install", "npm install",
                     "curl ", "wget ", "install_cmd", "auto_install")
    offenders: list[str] = []
    for py in SRC.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # só literais de string em código (não docstrings de módulo)
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                low = node.value.lower()
                for tok in banned_tokens:
                    if tok in low:
                        offenders.append(f"{py.name}:{node.lineno}:{tok}")
    assert offenders == []


def test_lsp_timeouts_bounded() -> None:
    """Timeouts existem e são finitos — nenhum wait indefinido."""
    from code_atlas.resolvers.lsp.lifecycle import INIT_TIMEOUT_S
    from code_atlas.resolvers.lsp.spawn import LspServerSpec

    assert 0 < INIT_TIMEOUT_S <= 60
    spec = LspServerSpec(name="t", cmd=("x",), languages=frozenset())
    assert 0 < spec.request_timeout_s <= 120


def test_no_unix_only_apis_in_src() -> None:
    """Paridade Windows: nada de fcntl/pwd/grp/termios/os.symlink fora
    de testes (resource já é guardado por try/except no bench)."""
    banned = {"fcntl", "pwd", "grp", "termios", "resource", "syslog"}
    offenders: list[str] = []
    for py in SRC.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders.extend(
                    f"{py.name}:{a.name}" for a in node.names
                    if a.name.split(".")[0] in banned
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in banned:
                    offenders.append(f"{py.name}:{node.module}")
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "symlink"
            ):
                offenders.append(f"{py.name}:{node.lineno} symlink")
    assert offenders == []


def test_resolvers_do_not_execute_repo_content() -> None:
    """Resolvers leem bytes/caminhos apenas — o comando LSP vem de
    config do usuário, nunca do repo indexado."""
    from code_atlas.resolvers.lsp.spawn import (
        LspServerSpec,
        SpawnError,
        spawn_server,
    )

    # um "comando" que pareceria vir do repo → não existe → erro tipado
    spec = LspServerSpec(
        name="repo-injected",
        cmd=("./node_modules/.bin/evil",),
        languages=frozenset({"python"}),
        enabled=True,
    )
    with pytest.raises(SpawnError):
        spawn_server(spec)
