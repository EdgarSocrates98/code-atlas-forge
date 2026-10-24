"""Detector de mudanças manifesto-vs-snapshot (lf-0019).

Compara o scan atual com o inventário do último snapshot READY e
produz um ``ChangeSet`` tipado — insumo do ``InvalidationPlanner``.

Regras inegociáveis:

- ``RENAMED`` **somente** quando um path deletado e um adicionado têm
  sha256 idêntico — evidência de conteúdo, nunca heurística de nome.
- Bump de versão de parser/extrator/grammar reclassifica os arquivos da
  linguagem afetada como ``MODIFIED``.
- Rename-of-case está reservado para lf-0021 e não é tratado aqui.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum

from code_atlas.discovery.models import ScanResult, SourceFile


class ChangeKind(StrEnum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    UNCHANGED = "unchanged"
    ANALYZER_CHANGED = "analyzer-changed"
    RESOLVER_CHANGED = "resolver-changed"


@dataclass(frozen=True)
class FileChange:
    """Mudança tipada de um arquivo, com razão auditável."""

    path: str
    kind: ChangeKind
    reason: str
    old_path: str = ""  # preenchido para RENAMED (path deletado)


@dataclass(frozen=True)
class ChangeSet:
    """Resultado do diff manifesto ↔ snapshot anterior."""

    changes: tuple[FileChange, ...]
    analyzer_changes: tuple[str, ...] = ()  # chaves de versão alteradas
    resolver_changes: tuple[str, ...] = ()

    def by_kind(self, kind: ChangeKind) -> tuple[FileChange, ...]:
        return tuple(c for c in self.changes if c.kind == kind)

    def for_path(self, path: str) -> FileChange | None:
        for c in self.changes:
            if c.path == path:
                return c
        return None

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in self.changes:
            out[str(c.kind)] = out.get(str(c.kind), 0) + 1
        return dict(sorted(out.items()))

    def to_dict(self) -> dict[str, object]:
        return {
            "changes": [
                {
                    "path": c.path,
                    "kind": str(c.kind),
                    "reason": c.reason,
                    **({"old_path": c.old_path} if c.old_path else {}),
                }
                for c in sorted(self.changes, key=lambda x: (x.path, str(x.kind)))
            ],
            "analyzer_changes": sorted(self.analyzer_changes),
            "resolver_changes": sorted(self.resolver_changes),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


@dataclass
class _PriorFile:
    path: str
    language: str
    sha256: str | None
    parse_status: str


def _affected_languages(
    prior_versions: dict[str, str], current_versions: dict[str, str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Chaves de versão alteradas → (analyzer_keys, resolver_keys).

    Versões ``parser:<lang>`` classificam arquivos daquela linguagem;
    ``resolver:<id>`` afeta relações de todas as linguagens suportadas.
    """
    analyzer: list[str] = []
    resolver: list[str] = []
    for key in sorted(set(prior_versions) | set(current_versions)):
        if prior_versions.get(key) == current_versions.get(key):
            continue
        if key.startswith("resolver:"):
            resolver.append(key)
        else:
            analyzer.append(key)
    return tuple(analyzer), tuple(resolver)


def detect_changes(
    scan: ScanResult,
    prior_inventory: list[dict[str, object]],
    prior_versions: dict[str, str],
    current_versions: dict[str, str],
) -> ChangeSet:
    """Diff do manifesto atual contra o inventário do snapshot anterior.

    ``prior_inventory`` vem de ``Store.snapshot_inventory`` — dicts com
    ``path``/``language``/``sha256``/``parse_status``.
    """
    prior = {
        str(r["path"]): _PriorFile(
            path=str(r["path"]),
            language=str(r["language"]),
            sha256=r["sha256"] if isinstance(r["sha256"], str) else None,
            parse_status=str(r.get("parse_status", "unparsed")),
        )
        for r in prior_inventory
    }
    current = {f.path: f for f in scan.files}

    analyzer_keys, resolver_keys = _affected_languages(
        prior_versions, current_versions
    )
    bumped_langs = {
        key.split(":", 1)[1] for key in analyzer_keys if key.startswith("parser:")
    }
    # analyzer não-linguístico mudou (scanner/scan_mode): sem prova de
    # extração idêntica → todos os arquivos pré-existentes reclassificam.
    global_bump = any(not k.startswith("parser:") for k in analyzer_keys)

    changes, added = _classify_present(
        current, prior, bumped_langs, global_bump,
        analyzer_keys, prior_versions, current_versions,
    )
    deleted = {p: pf for p, pf in prior.items() if p not in current}
    _mark_deleted(changes, deleted)
    _mark_renames(changes, added, deleted)
    _mark_added_bumps(changes, added, bumped_langs)

    return ChangeSet(
        changes=tuple(sorted(changes, key=lambda c: (c.path, str(c.kind)))),
        analyzer_changes=analyzer_keys,
        resolver_changes=resolver_keys,
    )


def _classify_present(
    current: dict[str, SourceFile],
    prior: dict[str, _PriorFile],
    bumped_langs: set[str],
    global_bump: bool,
    analyzer_keys: tuple[str, ...],
    prior_versions: dict[str, str],
    current_versions: dict[str, str],
) -> tuple[list[FileChange], dict[str, SourceFile]]:
    """Classifica arquivos presentes: ADDED / MODIFIED / UNCHANGED."""
    changes: list[FileChange] = []
    added: dict[str, SourceFile] = {}
    for path in sorted(current):
        f = current[path]
        prev = prior.get(path)
        if prev is None:
            added[path] = f
            changes.append(
                FileChange(path, ChangeKind.ADDED, "not in previous snapshot")
            )
            continue
        if global_bump or prev.language in bumped_langs:
            key = (
                f"parser:{prev.language}"
                if prev.language in bumped_langs
                else analyzer_keys[0]
            )
            changes.append(
                FileChange(
                    path, ChangeKind.MODIFIED,
                    f"analyzer version changed "
                    f"({prior_versions.get(key)} -> "
                    f"{current_versions.get(key)})",
                )
            )
        elif f.sha256 is None or prev.sha256 is None:
            # sem hash não há prova de conteúdo idêntico → conservador
            changes.append(
                FileChange(
                    path, ChangeKind.MODIFIED,
                    "content hash unavailable (cannot prove identical)",
                )
            )
        elif prev.sha256 != f.sha256:
            changes.append(
                FileChange(path, ChangeKind.MODIFIED, "content hash differs")
            )
        else:
            changes.append(
                FileChange(path, ChangeKind.UNCHANGED, "identical content hash")
            )
    return changes, added


def _mark_deleted(
    changes: list[FileChange], deleted: dict[str, _PriorFile]
) -> None:
    for path in sorted(deleted):
        changes.append(
            FileChange(path, ChangeKind.DELETED, "absent from current manifest")
        )


def _swap_added(changes: list[FileChange], path: str, new: FileChange) -> None:
    """Substitui a entrada ADDED de ``path`` (rename/bump a reclassifica).
    No-op se o path já foi reclassificado (ex.: virou RENAMED)."""
    for i, c in enumerate(changes):
        if c.path == path and c.kind == ChangeKind.ADDED:
            changes[i] = new
            return


def _mark_renames(
    changes: list[FileChange],
    added: dict[str, SourceFile],
    deleted: dict[str, _PriorFile],
) -> None:
    """Renames — evidência estrita: hash deletado == hash adicionado.
    Um path deletado casa no máximo um adicionado (primeiro em ordem)."""
    sha_to_deleted: dict[str, list[str]] = {}
    for p in sorted(deleted):
        sha = deleted[p].sha256
        if sha:
            sha_to_deleted.setdefault(sha, []).append(p)
    for path in sorted(added):
        sha = added[path].sha256
        if not sha:
            continue
        cands = sha_to_deleted.get(sha) or []
        if cands:
            old = cands.pop(0)
            _swap_added(
                changes, path,
                FileChange(
                    path, ChangeKind.RENAMED,
                    f"content-identical to deleted {old}", old_path=old,
                ),
            )


def _mark_added_bumps(
    changes: list[FileChange],
    added: dict[str, SourceFile],
    bumped_langs: set[str],
) -> None:
    """Arquivos novos de linguagem com bump levam razão enriquecida."""
    for path in sorted(added):
        f = added[path]
        if f.language in bumped_langs:
            _swap_added(
                changes, path,
                FileChange(
                    path, ChangeKind.ADDED,
                    "not in previous snapshot; analyzer bumped for "
                    f"{f.language}",
                ),
            )
