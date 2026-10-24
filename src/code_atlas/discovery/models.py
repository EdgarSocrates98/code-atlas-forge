"""Modelos do resultado de scan (Cycle 1).

Sem persistência: o scanner retorna estruturas de domínio; lf-0003 as grava.
O manifest é determinístico — nenhum timestamp nem caminho absoluto no corpo.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum


class ScanMode(StrEnum):
    """Como o scan operou."""

    GIT = "git"  # git binary + repo presentes
    GIT_REPO_NO_BINARY = "git_repo_no_binary"  # .git existe mas git ausente
    NO_GIT = "no_git"  # diretório comum


class WarningKind(StrEnum):
    """Categoria tipada de warning do scan."""

    SYMLINK_ESCAPE = "symlink_escape"
    LIMIT_FILES = "limit_files"
    LIMIT_DEPTH = "limit_depth"
    LIMIT_SIZE = "limit_size"
    TIMEOUT = "timeout"
    READ_ERROR = "read_error"
    GIT_ERROR = "git_error"


@dataclass(frozen=True)
class ScanWarning:
    """Warning tipado; `path` é relativo POSIX quando aplicável."""

    kind: WarningKind
    message: str
    path: str = ""


@dataclass(frozen=True)
class SourceFile:
    """Registro de um arquivo descoberto.

    `sha256` é None quando não hasheado (ignorado, oversize ou erro de leitura).
    """

    path: str  # relativo POSIX, canônico
    language: str
    size_bytes: int
    sha256: str | None
    ignored: bool = False


@dataclass(frozen=True)
class GitInfo:
    """Estado git detectado na raiz."""

    available: bool
    is_repo: bool
    branch: str = ""
    head_sha: str = ""
    dirty: bool = False
    is_worktree: bool = False
    remote_url: str = ""  # remote.origin.url — identidade preferida (lf-0021)
    root_commit: str = ""  # 1º commit (max-parents=0) — identidade fallback


@dataclass(frozen=True)
class ScanResult:
    """Resultado completo de um scan de diretório."""

    files: tuple[SourceFile, ...]
    git: GitInfo
    mode: ScanMode
    warnings: tuple[ScanWarning, ...] = ()
    secret_count: int = 0
    partial: bool = False
    truncated: bool = False
    elapsed_s: float = 0.0  # metadado de execução — fora do manifest
    hash_ms: int = 0  # tempo em _hash_file (lf-0031) — fora do manifest

    def manifest_bytes(self) -> bytes:
        """Serialização canônica e determinística do inventário.

        Não contém timestamps, caminhos absolutos nem metadados de execução:
        dois scans da mesma árvore produzem bytes idênticos.
        """
        payload = {
            "mode": str(self.mode),
            "git": {
                "available": self.git.available,
                "is_repo": self.git.is_repo,
                "branch": self.git.branch,
                "head_sha": self.git.head_sha,
                "dirty": self.git.dirty,
                "is_worktree": self.git.is_worktree,
            },
            "files": [
                {
                    "path": f.path,
                    "language": f.language,
                    "size_bytes": f.size_bytes,
                    "sha256": f.sha256,
                    "ignored": f.ignored,
                }
                for f in sorted(self.files, key=lambda x: x.path)
            ],
            "secret_count": self.secret_count,
            "partial": self.partial,
            "truncated": self.truncated,
            "warnings": [
                {"kind": str(w.kind), "message": w.message, "path": w.path}
                for w in sorted(self.warnings, key=lambda x: (x.path, str(x.kind)))
            ],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def manifest_sha256(self) -> str:
        """Fingerprint do manifest canônico."""
        return hashlib.sha256(self.manifest_bytes()).hexdigest()


@dataclass(frozen=True)
class ScanOptions:
    """Limites e comportamento do scanner."""

    max_files: int = 100_000
    max_file_size: int = 10 * 1024 * 1024  # 10 MiB
    max_depth: int = 40
    timeout_s: float | None = None
    include_untracked: bool = True
    hash_chunk_size: int = 64 * 1024


# Extensão → linguagem preliminar (Blueprint §4.1). Mapa intencionalmente curto;
# Tree-sitter (Cycle 2) assume a classificação definitiva.
LANGUAGE_BY_EXT: dict[str, str] = {
    ".py": "python", ".pyi": "python", ".js": "javascript", ".mjs": "javascript",
    ".cjs": "javascript", ".jsx": "javascript", ".ts": "typescript",
    ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin",
    ".kts": "kotlin", ".c": "c", ".h": "c", ".cpp": "cpp", ".cxx": "cpp",
    ".hpp": "cpp", ".cs": "csharp", ".rb": "ruby", ".php": "php",
    ".swift": "swift", ".m": "objective-c", ".scala": "scala", ".sh": "shell",
    ".bash": "shell", ".zsh": "shell", ".ps1": "powershell", ".sql": "sql",
    ".html": "html", ".htm": "html", ".css": "css", ".scss": "scss",
    ".md": "markdown", ".toml": "toml", ".yaml": "yaml", ".yml": "yaml",
    ".json": "json", ".xml": "xml", ".r": "r", ".lua": "lua", ".pl": "perl",
    ".ex": "elixir", ".exs": "elixir", ".erl": "erlang", ".hs": "haskell",
    ".clj": "clojure", ".dart": "dart", ".vue": "vue", ".svelte": "svelte",
}

# Diretórios/arquivos podados por padrão (Blueprint §4.1) — nunca inventariados.
ATLAS_DEFAULT_IGNORES: tuple[str, ...] = (
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    ".tox", ".nox", "dist", "build", ".idea", ".vscode", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".next", ".nuxt", "target", "coverage",
    ".eggs", "*.egg-info", ".factory-cache",
)

# Padrões de segredo (Blueprint ADR-014): excluídos de hash e inventário,
# contabilizados apenas em `secret_count`.
SECRET_PATTERNS: tuple[str, ...] = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.keystore",
    "*.jks", "id_rsa", "id_rsa.*", "id_dsa", "id_dsa.*", "id_ed25519",
    "id_ed25519.*", "credentials", "credentials.*", "*.credentials",
    ".netrc", ".npmrc", ".pypirc", "*.secret", "secrets.*",
)


def language_for(path: str) -> str:
    """Linguagem preliminar pela extensão; `unknown`/`other` quando ausente."""
    lower = path.lower()
    dot = lower.rfind(".")
    if dot < 0:
        return "other"
    return LANGUAGE_BY_EXT.get(lower[dot:], "other")


def _basename_match(name: str, patterns: tuple[str, ...]) -> bool:
    import fnmatch

    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def is_secret(path: str) -> bool:
    """True se o basename do path casa padrões de segredo."""
    return _basename_match(path.rsplit("/", 1)[-1], SECRET_PATTERNS)


def is_default_ignored(path: str) -> bool:
    """True se qualquer segmento/basename casa os ignores padrão do Atlas."""
    parts = path.split("/")
    name = parts[-1]
    if _basename_match(name, ATLAS_DEFAULT_IGNORES):
        return True
    vcs_dirs = {".git", ".hg", ".svn", "node_modules", "__pycache__"}
    return any(part in vcs_dirs for part in parts[:-1])
