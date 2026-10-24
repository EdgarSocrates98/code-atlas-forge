"""Registro de adaptadores: linguagem e extensão → adapter.

Linguagens não suportadas retornam ``None`` — nunca exceção. O registry
padrão cobre Python, TypeScript e TSX (Cycle 2); novos adaptadores se
registram sem tocar no registry (Open/Closed).
"""

from __future__ import annotations

from code_atlas.parsers.contracts import ParserAdapter


class ParserRegistry:
    """Mapa imutável-por-convenção de adaptadores disponíveis."""

    def __init__(self) -> None:
        self._by_language: dict[str, ParserAdapter] = {}
        self._by_ext: dict[str, ParserAdapter] = {}

    def register(self, adapter: ParserAdapter) -> None:
        self._by_language[adapter.language] = adapter
        for ext in adapter.extensions:
            self._by_ext[ext.lower()] = adapter

    def for_language(self, language: str) -> ParserAdapter | None:
        return self._by_language.get(language)

    def for_path(self, rel_path: str) -> ParserAdapter | None:
        """Adapter pela extensão do caminho (``.tsx`` antes de ``.ts``)."""
        lower = rel_path.lower()
        # extensões multi-part primeiro (.d.ts)
        for ext in sorted(self._by_ext, key=len, reverse=True):
            if lower.endswith(ext):
                return self._by_ext[ext]
        return None

    @property
    def languages(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_language))

    @classmethod
    def default(cls) -> ParserRegistry:
        """Registry completo do Cycle 2: python, typescript, tsx."""
        from code_atlas.parsers.languages.python import PythonAdapter
        from code_atlas.parsers.languages.tsx import TsxAdapter
        from code_atlas.parsers.languages.typescript import TypeScriptAdapter

        reg = cls()
        for adapter in (PythonAdapter(), TypeScriptAdapter(), TsxAdapter()):
            reg.register(adapter)
        return reg
