"""Testes do logging estruturado + redaction (lf-0005)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from code_atlas.observability import JsonFormatter, RedactionFilter, get_logger
from code_atlas.observability.logging import _redact_text


def test_redact_home_path() -> None:
    home = str(Path.home())
    out = _redact_text(f"failed at {home}/secret/x.py", home)
    assert home not in out
    assert "~/secret/x.py" in out


def test_redact_token_assignment() -> None:
    out = _redact_text("config api_key=sk-abc123 loaded", "")
    assert "sk-abc123" not in out
    assert "api_key=<REDACTED>" in out


def test_redact_bearer_and_password() -> None:
    for s in ("password: hunter2", "token=ghp_xxxx", "authorization: Bearer abc"):
        out = _redact_text(s, "")
        assert "<REDACTED>" in out


def test_redact_pem_block() -> None:
    pem = "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----"
    assert "PRIVATE KEY-----\\nabc" not in _redact_text(pem, "")
    assert "<REDACTED:pem>" in _redact_text(pem, "")


def test_redact_long_secret_like_string() -> None:
    token = "A" * 60
    assert token not in _redact_text(f"value {token} here", "")


def test_benign_text_untouched() -> None:
    text = "index complete: 42 files"
    assert _redact_text(text, str(Path.home())) == text


def test_filter_applies_to_record() -> None:
    filt = RedactionFilter(home="/home/alice")
    rec = logging.LogRecord("t", logging.INFO, __file__, 1,
                            "path /home/alice/x token=sek", (), None)
    filt.filter(rec)
    assert "/home/alice" not in rec.getMessage()


def test_json_formatter_emits_parseable_line() -> None:
    rec = logging.LogRecord("atlas", logging.WARNING, __file__, 1, "hello", (), None)
    line = JsonFormatter().format(rec)
    payload = json.loads(line)
    assert payload["msg"] == "hello"
    assert payload["level"] == "WARNING"


def test_get_logger_json_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODE_ATLAS_LOG_FORMAT", "json")
    monkeypatch.setenv("CODE_ATLAS_LOG_LEVEL", "DEBUG")
    logger = get_logger("atlas.test.json")
    assert logger.level == logging.DEBUG
    assert isinstance(logger.handlers[0].formatter, JsonFormatter)


def test_get_logger_idempotent() -> None:
    a = get_logger("atlas.test.same")
    b = get_logger("atlas.test.same")
    assert a is b and len(a.handlers) == 1


def test_logger_redacts_emitted_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CODE_ATLAS_LOG_LEVEL", "INFO")
    logger = get_logger("atlas.test.emit")
    logger.error("secret file %s", f"{Path.home()}/.env")
    err = capsys.readouterr().err
    assert str(Path.home()) not in err
    assert "~/.env" in err
