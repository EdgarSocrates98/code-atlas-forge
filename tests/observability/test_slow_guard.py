"""Slow-query guard ``CODE_ATLAS_SLOW_MS`` (lf-0141, Cycle 8 Phase 15)."""

from __future__ import annotations

import io
import logging
import time

import pytest

from code_atlas.observability.logging import (
    get_logger,
    log_if_slow,
    slow_ms,
    timed,
)


def test_slow_ms_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CODE_ATLAS_SLOW_MS", raising=False)
    assert slow_ms() is None


def test_slow_ms_parse() -> None:
    assert slow_ms({"CODE_ATLAS_SLOW_MS": "250"}) == 250.0
    assert slow_ms({"CODE_ATLAS_SLOW_MS": "0.5"}) == 0.5


@pytest.mark.parametrize("raw", ["", "abc", "0", "-5", "0.0"])
def test_slow_ms_desligado(raw: str) -> None:
    assert slow_ms({"CODE_ATLAS_SLOW_MS": raw}) is None


def _capture(name: str) -> tuple[logging.Logger, io.StringIO]:
    """Logger isolado escrevendo num stream — sem stderr global."""
    stream = io.StringIO()
    log = logging.getLogger(name)
    log.handlers.clear()
    log.addHandler(logging.StreamHandler(stream))
    log.setLevel(logging.DEBUG)
    log.propagate = False
    return log, stream


def test_log_if_slow_emite_acima_do_teto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODE_ATLAS_SLOW_MS", "10")
    log, stream = _capture("test.slow.over")
    log_if_slow(log, "query", time.monotonic() - 0.5)
    out = stream.getvalue()
    assert "slow query" in out
    assert "elapsed_ms=" in out
    assert "threshold_ms=10" in out


def test_log_if_slow_silencioso_abaixo_do_teto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODE_ATLAS_SLOW_MS", "60000")
    log, stream = _capture("test.slow.under")
    log_if_slow(log, "query", time.monotonic())
    assert stream.getvalue() == ""


def test_log_if_slow_silencioso_sem_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CODE_ATLAS_SLOW_MS", raising=False)
    log, stream = _capture("test.slow.off")
    log_if_slow(log, "query", time.monotonic() - 999)
    assert stream.getvalue() == ""


def test_timed_decorator_preserva_retorno_e_loga(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CODE_ATLAS_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("CODE_ATLAS_SLOW_MS", "1")
    # label único → get_logger cria o handler JÁ com o env do teste
    @timed("deco.unique1")
    def trabalho(x: int) -> int:
        time.sleep(0.01)
        return x * 2

    assert trabalho(21) == 42
    assert trabalho.__name__ == "trabalho"
    assert "slow deco.unique1" in capfd.readouterr().err


def test_timed_sem_env_nao_loga(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("CODE_ATLAS_SLOW_MS", raising=False)

    @timed("deco.unique2")
    def trabalho() -> None:
        time.sleep(0.01)

    trabalho()
    assert "slow" not in capfd.readouterr().err


def test_timed_propaga_excecao_e_loga(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODE_ATLAS_SLOW_MS", "0.0001")
    stream = io.StringIO()
    log = get_logger("code_atlas.deco.unique3")
    log.handlers.clear()
    log.addHandler(logging.StreamHandler(stream))
    log.setLevel(logging.DEBUG)

    @timed("deco.unique3")
    def falha() -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        falha()
    assert "slow deco.unique3" in stream.getvalue()
