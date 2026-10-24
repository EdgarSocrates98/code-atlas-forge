"""Ponto único de contato com o runtime Tree-sitter.

Somente módulos sob ``code_atlas.parsers`` podem importar ``tree_sitter``
— a fronteira é verificada por teste de arquitetura. Aqui ficam: carga de
gramáticas, criação de parsers com timeout, coleta de nós problema e
versões declaradas via ``importlib.metadata``.
"""

from __future__ import annotations

import importlib.metadata
import threading
import warnings
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import tree_sitter
from tree_sitter import Language, Node, Parser

from code_atlas.parsers.contracts import SourceSpan


@dataclass(frozen=True)
class LanguageSpec:
    """Gramática carregada + versões declaradas."""

    language: Language
    grammar_dist: str  # nome do pacote PyPI da gramática

    @property
    def grammar_version(self) -> str:
        return _pkg_version(self.grammar_dist)


_version_cache: dict[str, str] = {}
_version_lock = threading.Lock()


def _pkg_version(dist: str) -> str:
    """Versão do pacote — memoizada com lock (thread-safe).

    ``importlib.metadata.version`` não é segura no primeiro acesso
    concorrente (leitura de METADATA sob threads → OSError); versões
    de pacote são estáveis por processo, então o cache é correto.
    """
    cached = _version_cache.get(dist)
    if cached is None:
        with _version_lock:
            cached = _version_cache.get(dist)
            if cached is None:
                try:
                    cached = importlib.metadata.version(dist)
                except importlib.metadata.PackageNotFoundError:
                    cached = "unknown"
                _version_cache[dist] = cached
    return cached


def parser_version() -> str:
    """Versão do pacote ``tree-sitter`` instalado."""
    return _pkg_version("tree-sitter")


def load_language(factory: Callable[[], object], grammar_dist: str) -> LanguageSpec:
    """Carrega uma gramática pelo capsule provider do pacote."""
    return LanguageSpec(language=Language(factory()), grammar_dist=grammar_dist)


def parse_bytes(
    spec: LanguageSpec, content: bytes, timeout_us: int
) -> tree_sitter.Tree | None:
    """Parse real com deadline; None quando o timeout expira.

    ``timeout_micros`` é usado apesar de deprecado em 0.25: o caminho
    alternativo (callback read + ``progress_callback``) sofre segfault no
    binding 0.25.2 — registrado como dívida técnica para revisão quando
    a versão do binding subir. Nunca executa o código alvo.
    """
    parser = Parser(spec.language)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        # stub declara read-only; a API real aceita a atribuição (0.25.x)
        parser.timeout_micros = timeout_us  # type: ignore[misc]
    return parser.parse(content)


def span_of(node: Node) -> SourceSpan:
    """Converte Node do Tree-sitter para SourceSpan (linha 1-based)."""
    return SourceSpan(
        start_byte=node.start_byte,
        end_byte=node.end_byte,
        start_line=node.start_point.row + 1,
        start_col=node.start_point.column,
        end_line=node.end_point.row + 1,
        end_col=node.end_point.column,
    )


def iter_problem_nodes(root: Node, limit: int = 50) -> Iterator[tuple[Node, bool]]:
    """Percorre a árvore coletando (nó, is_missing) de ERROR/MISSING.

    Bounded por ``limit`` para não produzir diagnósticos gigantescos.
    """
    count = 0
    stack = [root]
    while stack and count < limit:
        node = stack.pop()
        if node.is_error or node.is_missing:
            yield node, node.is_missing
            count += 1
        # filhos ao final da pilha preservam ordem de visitação aproximada
        stack.extend(reversed(node.children))
    return


def node_count(root: Node) -> int:
    """Total de nós da árvore (métrica de diagnóstico)."""
    return root.descendant_count + 1
