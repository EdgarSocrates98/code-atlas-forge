"""Motor de parsing: orquestra requests, limites e isolamento de falhas.

Política de erro (Phase 2): falha por arquivo é registrada em
``ParseResult.diagnostics`` e nunca derruba o batch. Erros recuperáveis
(file-too-large, unsupported, timeout, parse-error) viram resultado com
``supported``/``has_error`` marcados; erro fatal = bug no adaptador —
isolado por arquivo como diagnóstico ``adapter-crash``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass

from code_atlas.parsers.contracts import (
    ParseDiagnostic,
    ParseRequest,
    ParseResult,
    SourceSpan,
)
from code_atlas.parsers.registry import ParserRegistry


@dataclass(frozen=True)
class ParseLimits:
    """Limites por arquivo — proteção contra conteúdo hostil."""

    max_file_size: int = 1 << 20  # 1 MiB
    timeout_us: int = 2_000_000
    max_diagnostics: int = 50


def _empty_result(
    path: str,
    language: str,
    content: bytes,
    diagnostic: ParseDiagnostic,
) -> ParseResult:
    return ParseResult(
        path=path,
        language=language,
        parser="",
        parser_version="",
        grammar_version="",
        content_hash=hashlib.sha256(content).hexdigest(),
        diagnostics=(diagnostic,),
        supported=False,
    )


class ParseEngine:
    """Orquestra ParseRequests via registry. Sem estado mutável exposto."""

    def __init__(
        self, registry: ParserRegistry | None = None, limits: ParseLimits | None = None
    ) -> None:
        self.registry = registry or ParserRegistry.default()
        self.limits = limits or ParseLimits()

    def parse(self, rel_path: str, content: bytes) -> ParseResult:
        """Parse de um arquivo; falhas viram diagnósticos, não exceções."""
        adapter = self.registry.for_path(rel_path)
        if adapter is None:
            return _empty_result(
                rel_path,
                "",
                content,
                ParseDiagnostic(
                    code="unsupported-language",
                    severity="info",
                    message="no parser adapter for this path",
                ),
            )
        if len(content) > self.limits.max_file_size:
            return _empty_result(
                rel_path,
                adapter.language,
                content,
                ParseDiagnostic(
                    code="file-too-large",
                    severity="warning",
                    message=f"{len(content)} bytes exceeds limit {self.limits.max_file_size}",
                ),
            )
        request = ParseRequest(
            path=rel_path,
            language=adapter.language,
            content=content,
            timeout_us=self.limits.timeout_us,
        )
        try:
            result = adapter.parse(request)
        except Exception as exc:
            return _empty_result(
                rel_path,
                adapter.language,
                content,
                ParseDiagnostic(
                    code="adapter-crash",
                    severity="error",
                    message=f"{type(exc).__name__}: {exc}",
                ),
            )
        if len(result.diagnostics) > self.limits.max_diagnostics:
            result = ParseResult(
                **{
                    **result.__dict__,
                    "diagnostics": result.diagnostics[: self.limits.max_diagnostics],
                }
            )
        return result

    def parse_batch(
        self, items: Iterable[tuple[str, bytes]]
    ) -> list[ParseResult]:
        """Parse de lote — isolamento garantido por arquivo."""
        return [self.parse(path, content) for path, content in items]


def span_of_point(
    start_byte: int, end_byte: int, line: int, col: int, end_line: int, end_col: int
) -> SourceSpan:
    """Helper de construção de span para testes."""
    return SourceSpan(start_byte, end_byte, line, col, end_line, end_col)
