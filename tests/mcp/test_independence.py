"""lf-0043 — doctor/manifest/independência de Forjas."""

from __future__ import annotations

import json
from pathlib import Path

from code_atlas import cli


def test_no_forge_imports_in_src() -> None:
    src = Path(__file__).parents[2] / "src" / "code_atlas"
    for py in src.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        assert "import forge" not in text, py
        assert "loop_factory" not in text, py


def test_mcp_manifest_does_not_write_host_config(capsys) -> None:
    rc = cli.main(["mcp", "manifest", "--host", "claude-code"])
    out = capsys.readouterr()
    assert rc == 0
    d = json.loads(out.out)
    assert d["mcpServers"]["code-atlas"]["args"] == ["mcp"]
    assert "nunca escreve" in out.err  # só emite + instrui


def test_mcp_doctor_json(capsys) -> None:
    rc = cli.main(["mcp", "doctor", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    for k in ("executable", "python", "db_path", "db_exists",
              "sqlite_vec"):
        assert k in out
