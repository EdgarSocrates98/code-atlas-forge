"""Adaptador TSX — mesma extração do TypeScript, gramática JSX-enabled.

``tree_sitter_typescript.language_tsx()`` habilita constructs JSX; o
extractor compartilhado já registra referências ``jsx``. Linguagem
reportada como ``tsx`` para distinguir capacidade e gramática.
"""

from __future__ import annotations

from typing import Any

import tree_sitter_typescript

from code_atlas.parsers.languages.typescript import TypeScriptAdapter


class TsxAdapter(TypeScriptAdapter):
    """TSX (.tsx) — gramática tsx do pacote tree-sitter-typescript."""

    language: str = "tsx"
    extensions: tuple[str, ...] = (".tsx",)

    @property
    def adapter_id(self) -> str:
        return "tree-sitter-tsx"

    def _factory(self) -> Any:
        return tree_sitter_typescript.language_tsx
