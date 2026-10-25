"""Logging estruturado com redaction (Blueprint §9, ADR-014).

Sem telemetria, sem egress — logs vão apenas para stderr local.
Formato: texto simples por padrão, JSON-lines com ``CODE_ATLAS_LOG_FORMAT=json``.
Nível: ``CODE_ATLAS_LOG_LEVEL`` (default WARNING).

O filtro de redaction mascara: (a) caminhos absolutos dentro de $HOME,
(b) padrões de segredo (tokens, chaves, assignments sensíveis).

Slow-query guard (lf-0141, Cycle 8 Phase 15): ``CODE_ATLAS_SLOW_MS``
define um teto de latência em milissegundos; chamadas instrumentadas
que o excedem emitem ``DEBUG`` via :func:`log_if_slow` / :func:`timed`.
Ausente, inválido ou ``<=0`` = guard desligado com custo zero (um
``os.environ.get`` por chamada, nenhum clock extra além do já medido).
"""

from __future__ import annotations

import functools
import json
import logging
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import ParamSpec, TypeVar

ENV_FORMAT = "CODE_ATLAS_LOG_FORMAT"
ENV_LEVEL = "CODE_ATLAS_LOG_LEVEL"
ENV_SLOW_MS = "CODE_ATLAS_SLOW_MS"

_TOKEN_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|passwd|authorization|bearer|"
    r"aws[_-]?access|private[_-]?key|credential)\s*[:=]\s*\S+"
)
_LONG_SECRET_RE = re.compile(r"\b[A-Za-z0-9+/=_\-]{40,}\b")
_PEM_RE = re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----.*?-----END [A-Z ]+PRIVATE KEY-----",
                     re.DOTALL)
# Controles C0/C1/DEL → escape ``\xNN`` (lf-0153, cycle-8.1 Phase 10):
# linha de request hostil (``GET /\x1b[31m``) ou filename com ``\n``
# podiam forjar linhas/sequências ANSI no stderr — logs são single-line
# por contrato, então TODO caractere de controle vira escape visível.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def escape_control(text: str) -> str:
    """Caracteres de controle viram ``\\xNN`` — anti log-injection."""
    return _CONTROL_RE.sub(lambda m: f"\\x{ord(m.group(0)):02x}", text)


def _redact_text(text: str, home: str) -> str:
    """Aplica as regras de mascaramento a uma string."""
    text = escape_control(text)
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
        if record.exc_info and record.exc_text is None:
            # lf-0153: traceback text contorna msg/args — pré-renderiza
            # escapado para que o Formatter não emita controles crus
            # (filename ``a\nb.py`` numa exceção forjaria linhas).
            record.exc_text = escape_control(
                logging.Formatter().formatException(record.exc_info))
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


# --- slow-query guard (lf-0141) -----------------------------------------
#
# Guard opt-in por env: ``CODE_ATLAS_SLOW_MS`` é o teto em ms. As
# fronteiras instrumentadas (despacho HTTP em ``api.handlers.handle``,
# ``SearchEngine.search_scoped``) medem com ``time.monotonic`` — o
# guard só decide se o valor já medido vira uma linha DEBUG.

def slow_ms(env: dict[str, str] | None = None) -> float | None:
    """Teto do slow-query guard em ms; ``None`` = desligado.

    ``CODE_ATLAS_SLOW_MS`` ausente/vazio, não-numérico ou ``<= 0``
    desliga o guard — opt-in explícito, default de custo zero.
    """
    raw = (env if env is not None else os.environ).get(ENV_SLOW_MS, "")
    if not raw:
        return None
    try:
        ms = float(raw)
    except ValueError:
        return None
    return ms if ms > 0 else None


def log_if_slow(
    logger: logging.Logger, label: str, t0: float, detail: str = ""
) -> None:
    """Emite ``DEBUG`` quando ``time.monotonic() - t0`` excede o teto.

    ``detail`` é contexto da chamada (rota, estratégia) — passa pelo
    ``RedactionFilter`` do logger como qualquer mensagem.
    """
    ms = slow_ms()
    if ms is None:
        return
    elapsed = (time.monotonic() - t0) * 1000
    # Windows: clock monotônico tem resolução ~15.6ms — elapsed=0 pode
    # ser "instantâneo" ou "menor que um tick". Somar a resolução torna
    # o guard conservador: só garante não-slow quando mesmo o pior caso
    # dentro da granularidade ficaria abaixo do teto.
    res_ms = time.get_clock_info("monotonic").resolution * 1000
    if elapsed + res_ms > ms:
        suffix = f" {detail}" if detail else ""
        logger.debug("slow %s elapsed_ms=%.1f threshold_ms=%.0f%s",
                     label, elapsed, ms, suffix)


_P = ParamSpec("_P")
_R = TypeVar("_R")


def timed(label: str) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Decorator do slow-query guard — mede a chamada e delega a
    ``log_if_slow``. O logger é ``code_atlas.<label>`` (mesmo filtro
    de redaction de qualquer ``get_logger``)."""
    logger = get_logger(f"code_atlas.{label}")

    def deco(fn: Callable[_P, _R]) -> Callable[_P, _R]:
        @functools.wraps(fn)
        def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            t0 = time.monotonic()
            try:
                return fn(*args, **kwargs)
            finally:
                log_if_slow(logger, label, t0)

        return wrapper

    return deco


__all__ = [
    "JsonFormatter",
    "RedactionFilter",
    "escape_control",
    "get_logger",
    "log_if_slow",
    "slow_ms",
    "timed",
]
