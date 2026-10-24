"""Logging estruturado com redaction (Blueprint §9, ADR-014).

Sem telemetria, sem egress — logs vão apenas para stderr local.
Formato: texto simples por padrão, JSON-lines com ``CODE_ATLAS_LOG_FORMAT=json``.
Nível: ``CODE_ATLAS_LOG_LEVEL`` (default WARNING).

O filtro de redaction mascara: (a) caminhos absolutos dentro de $HOME,
(b) padrões de segredo (tokens, chaves, assignments sensíveis).
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

ENV_FORMAT = "CODE_ATLAS_LOG_FORMAT"
ENV_LEVEL = "CODE_ATLAS_LOG_LEVEL"

_TOKEN_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|passwd|authorization|bearer|"
    r"aws[_-]?access|private[_-]?key|credential)\s*[:=]\s*\S+"
)
_LONG_SECRET_RE = re.compile(r"\b[A-Za-z0-9+/=_\-]{40,}\b")
_PEM_RE = re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----.*?-----END [A-Z ]+PRIVATE KEY-----",
                     re.DOTALL)


def _redact_text(text: str, home: str) -> str:
    """Aplica as regras de mascaramento a uma string."""
    if home and home in text:
        text = text.replace(home, "~")
    text = _PEM_RE.sub("<REDACTED:pem>", text)
    text = _TOKEN_RE.sub(lambda m: m.group(1) + "=<REDACTED>", text)
    # Sequências longas tipo chave/token só mascaram se parecerem segredo.
    text = _LONG_SECRET_RE.sub(lambda m: "<REDACTED>", text)
    return text


class RedactionFilter(logging.Filter):
    """Remove home paths e segredos de todos os records emitidos."""

    def __init__(self, home: str | None = None) -> None:
        super().__init__()
        self._home = home or str(Path.home())

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _redact_text(record.msg, self._home)
        if record.args:
            record.args = tuple(
                _redact_text(str(a), self._home) if isinstance(a, (str, Path)) else a
                for a in record.args
            ) if isinstance(record.args, tuple) else record.args
        return True


class JsonFormatter(logging.Formatter):
    """Serializa records como JSON-lines (uma linha por evento)."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def get_logger(name: str) -> logging.Logger:
    """Logger configurado com redaction; idempotente por nome."""
    logger = logging.getLogger(name)
    if getattr(logger, "_atlas_configured", False):
        return logger
    logger.setLevel(os.environ.get(ENV_LEVEL, "WARNING").upper())
    handler = logging.StreamHandler()
    handler.addFilter(RedactionFilter())
    if os.environ.get(ENV_FORMAT, "").lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    logger._atlas_configured = True  # type: ignore[attr-defined]
    return logger


__all__ = ["JsonFormatter", "RedactionFilter", "get_logger"]
