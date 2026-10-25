"""``code-atlas index --verbose`` — progresso em stderr (lf-0141).

A flag liga o ``ProgressFn`` do coordinator: linhas ``[index]``
com ms por estágio e files/s do parse. stdout — inclusive o payload
``--json`` — nunca é tocado pelo progresso.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_atlas import cli


def test_index_verbose_progresso_em_stderr(
    ws: Path, sample: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli.main(["index", str(sample), "--verbose"])
    assert rc == 0
    cap = capsys.readouterr()
    assert "[index] stage" in cap.err
    assert "stage=discovery" in cap.err
    assert "stage=parse" in cap.err
    assert "parse-progress" in cap.err
    assert "files_per_sec=" in cap.err
    # humano continua no stdout
    assert "snapshot #" in cap.out


def test_index_verbose_nao_polui_json(
    ws: Path, sample: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli.main(["index", str(sample), "--verbose", "--json"])
    assert rc == 0
    cap = capsys.readouterr()
    payload = json.loads(cap.out)  # stdout segue parseável
    assert payload["status"] == "success"
    assert "[index]" in cap.err


def test_index_sem_verbose_stderr_limpo(
    ws: Path, sample: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli.main(["index", str(sample)])
    assert rc == 0
    assert "[index]" not in capsys.readouterr().err


def test_index_verbose_noop(
    ws: Path, sample: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["index", str(sample)]) == 0
    capsys.readouterr()
    rc = cli.main(["index", str(sample), "--verbose"])
    assert rc == 0
    assert "[index] noop" in capsys.readouterr().err
