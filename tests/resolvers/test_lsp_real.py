"""lf-0035 — E2E com servidor LSP REAL (opt-in por presença de binário).

Roda quando ``pyright-langserver`` está no PATH (ou
``CODE_ATLAS_PYRIGHT`` aponta o binário); ausente → skip com razão
``NOT RUN`` explícita — CI e instalação mínima nunca exigem Node/npm.

Setup de desenvolvimento: ``npm install -g pyright@1.1.413``
(versão pinada no docs/cycle-4-baseline.md).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from code_atlas.resolvers.lsp import LspServerSpec, make_client


def _pyright() -> str | None:
    env = os.environ.get("CODE_ATLAS_PYRIGHT")
    return env or shutil.which("pyright-langserver")


pytestmark = pytest.mark.skipif(
    _pyright() is None,
    reason="pyright-langserver ausente — real LSP E2E NOT RUN",
)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "pkg" / "util.py").write_text(
        "def helper():\n    return 1\n"
    )
    (repo / "pkg" / "engine.py").write_text(
        "from pkg.util import helper\n\n"
        "def run():\n"
        "    return helper()\n"
    )
    return repo


def test_pyright_real_handshake_document_symbol(tmp_path: Path) -> None:
    """Handshake real + documentSymbol: pyright devolve os símbolos
    do arquivo — prova de que transporte/lifecycle funcionam contra um
    servidor de produção, não só o fake."""
    repo = _repo(tmp_path)
    spec = LspServerSpec(
        name="pyright",
        cmd=(_pyright(), "--stdio"),  # type: ignore[arg-type]
        root_uri=repo.as_uri(),
        enabled=True,
        request_timeout_s=30.0,
        languages=frozenset({"python"}),
    )
    client = make_client(spec)
    try:
        assert client is not None and client.available
        caps = client.capabilities
        assert "pyright" in (caps.server_name or "").lower()
        assert caps.document_symbol
        uri = (repo / "pkg" / "util.py").as_uri()
        client.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "python",
                    "version": 1,
                    "text": (repo / "pkg" / "util.py").read_text(),
                }
            },
        )
        symbols = client.document_symbol(uri)
        names = {
            s.get("name")
            for s in (symbols or [])
            if isinstance(s, dict)
        }
        assert "helper" in names
    finally:
        if client is not None:
            client.shutdown()
