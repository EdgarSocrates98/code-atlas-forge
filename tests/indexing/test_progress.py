"""Callback de progresso do ``IndexCoordinator`` (lf-0141, Phase 15).

``index(progress=fn)`` emite eventos ``"stage"`` (mesmos nomes das
chaves ``stage_ms_*`` persistidas no job), ``"parse-progress"`` com
throughput e ``"noop"`` — telemetria local para o operador (stderr
da CLI), nunca rede. Default ``None`` = zero custo, contrato antigo.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from code_atlas.indexing import IndexCoordinator
from code_atlas.storage import connect, migrate


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    (r / "mod.py").write_text("def helper(x):\n    return x + 1\n")
    (r / "caller.py").write_text(
        "from mod import helper\n\n\ndef main():\n    return helper(1)\n")
    return r


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    yield conn
    conn.close()


def _collect() -> tuple[list[tuple[str, dict[str, Any]]], Any]:
    events: list[tuple[str, dict[str, Any]]] = []

    def cb(event: str, fields: Any) -> None:
        events.append((event, dict(fields)))

    return events, cb


def test_stage_events_cobrem_o_pipeline(
    db: sqlite3.Connection, repo: Path
) -> None:
    events, cb = _collect()
    outcome = IndexCoordinator(db).index(repo, progress=cb)
    assert outcome.status == "success"
    stages = [f["stage"] for e, f in events if e == "stage"]
    for s in ("discovery", "hashing", "invalidation", "parse",
              "graph_build", "semantic", "domains", "validation",
              "persist_rows", "fk_check", "publish_gc",
              "persist_publish"):
        assert s in stages, f"stage {s} ausente em {stages}"
    # todo stage carrega ms inteiro não-negativo — medido, nunca inventado
    for e, f in events:
        if e == "stage":
            assert isinstance(f["ms"], int) and f["ms"] >= 0


def test_parse_progress_tem_files_per_sec(
    db: sqlite3.Connection, repo: Path
) -> None:
    events, cb = _collect()
    IndexCoordinator(db).index(repo, progress=cb)
    prog = [f for e, f in events if e == "parse-progress"]
    assert prog, "nenhum parse-progress emitido"
    final = prog[-1]
    assert final["parsed"] == final["total"] == 2
    assert final["files_per_sec"] > 0


def test_discovery_carrega_contagens(
    db: sqlite3.Connection, repo: Path
) -> None:
    events, cb = _collect()
    IndexCoordinator(db).index(repo, progress=cb)
    disc = [f for e, f in events
            if e == "stage" and f["stage"] == "discovery"]
    assert disc and disc[0]["files"] == 2
    assert disc[0]["secrets"] == 0


def test_noop_emite_evento(db: sqlite3.Connection, repo: Path) -> None:
    coord = IndexCoordinator(db)
    first = coord.index(repo)
    assert first.status == "success"
    events, cb = _collect()
    second = coord.index(repo, progress=cb)
    assert second.status == "noop"
    noop = [f for e, f in events if e == "noop"]
    assert noop and noop[0]["snapshot_id"] == first.snapshot_id


def test_default_sem_callback_zero_custo(
    db: sqlite3.Connection, repo: Path
) -> None:
    outcome = IndexCoordinator(db).index(repo)
    assert outcome.status == "success"
    # stats persistidos continuam independentes do callback
    assert any(k.startswith("stage_ms_") for k in outcome.stats)
