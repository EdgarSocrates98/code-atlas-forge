"""Parser mínimo de `.gitignore` (fallback sem git).

Best-effort: suporta `*`, `?`, `[...]`, `**`, âncora `/`, negação `!` e
padrões de diretório com `/` final. Escopo é o diretório do `.gitignore`.
Semântica: a última regra que casa decide (ordem do arquivo, raiz → folha).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


def _translate(pattern: str) -> str:
    """Converte um glob gitignore em regex sobre path relativo POSIX."""
    out = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if pattern[i : i + 2] == "**":
                if pattern[i : i + 3] == "**/":
                    out.append("(?:.*/)?")
                    i += 3
                    continue
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = pattern.find("]", i + 1)
            if j == -1:
                out.append(re.escape(c))
            else:
                out.append(pattern[i : j + 1])
                i = j
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


@dataclass(frozen=True)
class _Rule:
    regex: re.Pattern[str]
    negate: bool
    dir_only: bool


class GitIgnoreScope:
    """Regras de um único `.gitignore`, aplicáveis a paths relativos a seu dir."""

    def __init__(self, base: str, rules: list[_Rule]) -> None:
        self.base = base  # dir relativo POSIX onde o .gitignore vive ("" = raiz)
        self.rules = rules

    @classmethod
    def parse(cls, base: str, text: str) -> GitIgnoreScope:
        rules: list[_Rule] = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            negate = line.startswith("!")
            if negate:
                line = line[1:]
            dir_only = line.endswith("/")
            line = line.rstrip("/")
            anchored = line.startswith("/")
            line = line.lstrip("/")
            if not line:
                continue
            if "/" not in line and not anchored:
                # Sem barra: casa basename em qualquer profundidade do escopo.
                regex = re.compile(rf"(?:^|/){_translate(line)}$")
            else:
                regex = re.compile(rf"^{_translate(line)}$")
            rules.append(_Rule(regex=regex, negate=negate, dir_only=dir_only))
        return cls(base=base, rules=rules)

    def matches(self, path: str, is_dir: bool) -> bool | None:
        """True/False se decidido por este escopo; None se nenhuma regra casa."""
        rel = path[len(self.base) :].lstrip("/") if self.base else path
        if not rel:
            return None
        decision: bool | None = None
        for rule in self.rules:
            if rule.dir_only and not is_dir:
                continue
            if rule.regex.match(rel):
                decision = not rule.negate  # último match vence
        return decision


class GitIgnoreStack:
    """Pilha de escopos `.gitignore`; a decisão mais profunda e tardia vence."""

    def __init__(self) -> None:
        self._scopes: list[GitIgnoreScope] = []

    def add_file(self, base_dir: str, path: Path) -> None:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        self._scopes.append(GitIgnoreScope.parse(base_dir, text))

    def is_ignored(self, path: str, is_dir: bool) -> bool:
        """True se ignorado; o escopo mais profundo que decide vence."""
        for scope in reversed(self._scopes):
            if scope.base and not path.startswith(scope.base + "/"):
                continue
            decision = scope.matches(path, is_dir)
            if decision is not None:
                return decision
        return False
