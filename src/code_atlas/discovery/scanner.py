"""Scanner Git-aware de inventário (Blueprint §4.1 / ADR-014).

Perímetro de segurança do Code Atlas: decide o que entra no inventário.
Nunca executa, importa ou faz shell-out para código do repositório varrido —
apenas invocações do binário `git` contra a raiz são permitidas (argv, sem
shell). Nada é escrito dentro do diretório varrido.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from code_atlas.discovery.gitignore import GitIgnoreStack
from code_atlas.discovery.models import (
    GitInfo,
    ScanMode,
    ScanOptions,
    ScanResult,
    ScanWarning,
    SourceFile,
    WarningKind,
    is_default_ignored,
    is_secret,
    language_for,
)


def _git(root: Path, args: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess[bytes]:
    """Invoca `git` com argv seguro (sem shell) na raiz do scan."""
    git_bin = shutil.which("git") or "git"
    return subprocess.run(
        [git_bin, "-C", str(root), *args],
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _git_lines(root: Path, args: list[str]) -> list[str]:
    """`git` com saída separada por NUL → paths relativos POSIX."""
    try:
        out = _git(root, args)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if out.returncode != 0:
        return []
    return [p for p in out.stdout.decode("utf-8", "replace").split("\0") if p]


def probe_git(root: Path, git_available: bool | None = None) -> GitInfo:
    """Detecta estado git da raiz: repo, branch, HEAD, dirty, worktree.

    `git_available=None` usa `shutil.which`; testes podem injetar False para
    simular git ausente.
    """
    if git_available is None:
        git_available = shutil.which("git") is not None
    has_dot_git = (root / ".git").exists()
    if not git_available:
        return GitInfo(available=False, is_repo=has_dot_git)
    try:
        inside = _git(root, ["rev-parse", "--is-inside-work-tree"])
    except (OSError, subprocess.TimeoutExpired):
        return GitInfo(available=True, is_repo=has_dot_git)
    if inside.returncode != 0 or inside.stdout.strip() != b"true":
        return GitInfo(available=True, is_repo=False)

    def _out(args: list[str]) -> str:
        try:
            res = _git(root, args)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return res.stdout.decode("utf-8", "replace").strip() if res.returncode == 0 else ""

    branch = _out(["symbolic-ref", "--quiet", "--short", "HEAD"])
    head = _out(["rev-parse", "HEAD"])
    dirty = bool(_git_lines(root, ["status", "--porcelain", "-z"]))
    git_dir = _out(["rev-parse", "--git-dir"])
    common = _out(["rev-parse", "--git-common-dir"])
    is_worktree = bool(git_dir and common and git_dir != common) or not (root / ".git").is_dir()
    return GitInfo(
        available=True,
        is_repo=True,
        branch=branch,
        head_sha=head,
        dirty=dirty,
        is_worktree=is_worktree,
    )


def _hash_file(path: Path, chunk_size: int) -> str | None:
    """SHA-256 por streaming; None em erro de leitura."""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            while chunk := fh.read(chunk_size):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _rel_posix(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _depth(rel: str) -> int:
    return len(rel.split("/"))


class _Budget:
    """Rastreia limites, warnings e deadline do scan."""

    def __init__(self, options: ScanOptions) -> None:
        self.options = options
        self.warnings: list[ScanWarning] = []
        self.truncated = False
        self.timed_out = False
        self.deadline = (
            time.monotonic() + options.timeout_s if options.timeout_s is not None else None
        )
        self._depth_warned: set[str] = set()

    def check_timeout(self) -> bool:
        if self.deadline is not None and time.monotonic() > self.deadline:
            if not self.timed_out:
                self.warnings.append(
                    ScanWarning(WarningKind.TIMEOUT, f"scan timeout {self.options.timeout_s}s")
                )
            self.timed_out = True
            return True
        return False

    def note_depth_breach(self, rel: str) -> None:
        parent = rel.rsplit("/", 1)[0]
        if parent not in self._depth_warned:
            self._depth_warned.add(parent)
            self.warnings.append(
                ScanWarning(
                    WarningKind.LIMIT_DEPTH,
                    f"depth limit {self.options.max_depth} exceeded",
                    parent,
                )
            )


@dataclass
class _Accumulator:
    files: list[SourceFile]
    secret_count: int = 0
    count: int = 0


def _classify_and_hash(
    abs_path: Path,
    rel: str,
    ignored: bool,
    options: ScanOptions,
    acc: _Accumulator,
    budget: _Budget,
) -> None:
    """Monta o registro SourceFile aplicando segredos, limites e hash."""
    if is_secret(rel):
        acc.secret_count += 1
        return
    if is_default_ignored(rel):
        return
    if _depth(rel) > options.max_depth:
        budget.note_depth_breach(rel)
        return
    if acc.count >= options.max_files:
        if not budget.truncated:
            budget.truncated = True
            budget.warnings.append(
                ScanWarning(
                    WarningKind.LIMIT_FILES,
                    f"file count limit {options.max_files} reached",
                    rel,
                )
            )
        return
    try:
        size = abs_path.stat().st_size
    except OSError as exc:
        budget.warnings.append(ScanWarning(WarningKind.READ_ERROR, str(exc), rel))
        return
    acc.count += 1
    if ignored:
        return  # ignorados são excluídos do inventário (gitignore / fallback)
    if size > options.max_file_size:
        acc.files.append(
            SourceFile(path=rel, language=language_for(rel), size_bytes=size, sha256=None)
        )
        return
    digest = _hash_file(abs_path, options.hash_chunk_size)
    if digest is None:
        budget.warnings.append(
            ScanWarning(WarningKind.READ_ERROR, "unreadable or vanished during scan", rel)
        )
    acc.files.append(
        SourceFile(path=rel, language=language_for(rel), size_bytes=size, sha256=digest)
    )


def _symlink_escapes(path: Path, root_real: Path) -> bool:
    """True se `path` é symlink cujo alvo resolve fora de `root_real`."""
    try:
        return not path.resolve().is_relative_to(root_real)
    except OSError:
        return True


def _filter_dirs(
    d: Path, rel_dir: str, dirnames: list[str], stack: GitIgnoreStack,
    root_real: Path, options: ScanOptions, budget: _Budget,
) -> list[str]:
    """Prune de diretórios: symlinks de dir, ignores e profundidade."""
    kept = []
    for name in dirnames:
        sub = d / name
        rel = f"{rel_dir}/{name}" if rel_dir else name
        if sub.is_symlink():
            if _symlink_escapes(sub, root_real):
                budget.warnings.append(
                    ScanWarning(WarningKind.SYMLINK_ESCAPE, "dir symlink escapes root", rel)
                )
            continue  # nunca desce em symlink de diretório
        if is_default_ignored(rel) or stack.is_ignored(rel, is_dir=True):
            continue
        if _depth(rel) > options.max_depth:
            budget.note_depth_breach(rel + "/")
            continue
        kept.append(name)
    return sorted(kept)


def _walk_paths(
    root: Path, root_real: Path, options: ScanOptions, budget: _Budget
) -> Iterator[tuple[Path, str, bool]]:
    """Gera (abs_path, rel_posix, ignored) via os.walk + ignores best-effort."""
    stack = GitIgnoreStack()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        if budget.check_timeout():
            return
        d = Path(dirpath)
        rel_dir = _rel_posix(root, d) if d != root else ""

        gi = d / ".gitignore"
        if gi.is_file() and not gi.is_symlink():
            stack.add_file(rel_dir, gi)

        dirnames[:] = _filter_dirs(d, rel_dir, dirnames, stack, root_real, options, budget)

        for name in sorted(filenames):
            f = d / name
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if f.is_symlink():
                if _symlink_escapes(f, root_real):
                    budget.warnings.append(
                        ScanWarning(
                            WarningKind.SYMLINK_ESCAPE, "file symlink escapes root", rel
                        )
                    )
                    continue
                if not f.exists():
                    continue  # symlink quebrado ou alvo ausente
            ignored = stack.is_ignored(rel, is_dir=False)
            yield f, rel, ignored


def _git_paths(
    root: Path, options: ScanOptions, budget: _Budget, git_info: GitInfo
) -> Iterator[tuple[Path, str, bool]]:
    """Gera (abs_path, rel_posix, ignored) via `git ls-files`."""
    tracked = _git_lines(root, ["ls-files", "-z", "-c"])
    if not tracked and not git_info.head_sha:
        tracked = []
    untracked = (
        _git_lines(root, ["ls-files", "-z", "-o", "--exclude-standard"])
        if options.include_untracked
        else []
    )
    ignored = set(_git_lines(root, ["ls-files", "-z", "-o", "-i", "--exclude-standard"]))
    for rel in sorted(tracked):
        yield root / rel, rel, False
    for rel in sorted(untracked):
        if budget.check_timeout():
            return
        yield root / rel, rel, rel in ignored
    for rel in sorted(ignored):
        yield root / rel, rel, True


def scan(
    root: Path, options: ScanOptions | None = None, git_available: bool | None = None
) -> ScanResult:
    """Varre `root` e retorna um `ScanResult` determinístico.

    Ctrl-C ou timeout retorna resultado `partial=True` com o acumulado.
    """
    options = options or ScanOptions()
    started = time.monotonic()
    root = Path(root).resolve()
    root_real = root  # já resolvido
    budget = _Budget(options)
    acc = _Accumulator(files=[])

    git_info = probe_git(root, git_available)
    if git_info.available and git_info.is_repo:
        mode = ScanMode.GIT
        path_iter = _git_paths(root, options, budget, git_info)
    else:
        mode = (
            ScanMode.GIT_REPO_NO_BINARY
            if git_info.is_repo
            else ScanMode.NO_GIT
        )
        path_iter = _walk_paths(root, root_real, options, budget)

    partial = False
    try:
        for abs_path, rel, ignored in path_iter:
            if budget.check_timeout():
                partial = True
                break
            if abs_path.is_dir() or not abs_path.exists():
                continue
            _classify_and_hash(abs_path, rel, ignored, options, acc, budget)
    except KeyboardInterrupt:
        partial = True
        budget.warnings.append(
            ScanWarning(WarningKind.TIMEOUT, "interrupted (KeyboardInterrupt)")
        )
    partial = partial or budget.timed_out

    return ScanResult(
        files=tuple(sorted(acc.files, key=lambda f: f.path)),
        git=git_info,
        mode=mode,
        warnings=tuple(budget.warnings),
        secret_count=acc.secret_count,
        partial=partial,
        truncated=budget.truncated,
        elapsed_s=time.monotonic() - started,
    )
