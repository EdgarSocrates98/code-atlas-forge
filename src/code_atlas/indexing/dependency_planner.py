"""Planner de invalidação dependency-aware (lf-0019).

Consome o ``ChangeSet`` + arestas IMPORTS do snapshot anterior e produz
um ``IndexPlan`` inspecionável **antes** de qualquer execução.

Ações por arquivo:

- ``reparse`` — conteúdo/presença mudou (added/modified/renamed ou
  analyzer bumped); símbolos e relações precisam ser refeitos.
- ``revalidate-relations`` — fonte inalterada mas um módulo do qual ela
  depende (direta ou transitivamente) mudou; relações precisam ser
  reavaliadas mesmo sem re-leitura conceitual do conteúdo.
- ``reuse`` — inalterada e não afetada: ocorrências e relações copiadas
  do snapshot anterior.
- ``drop`` — deletada.

Postura conservadora: informação de dependência desconhecida ou
incompleta → reavaliar. Ciclos são atravessados com conjunto de
visitados. Nenhuma relação pode ficar *stale* após mudança de
dependência — invalidação é superconjunto do necessário, nunca sub.
"""

from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass
from enum import StrEnum

from code_atlas.indexing.change_detector import ChangeKind, ChangeSet


class PlanAction(StrEnum):
    REPARSE = "reparse"
    REVALIDATE_RELATIONS = "revalidate-relations"
    REUSE = "reuse"
    DROP = "drop"


@dataclass(frozen=True)
class PlannedFile:
    path: str
    action: PlanAction
    reason: str


@dataclass(frozen=True)
class IndexPlan:
    """Plano de indexação serializável e consultável (lf-0019)."""

    files: tuple[PlannedFile, ...]
    incremental: bool = True

    def action_for(self, path: str) -> PlanAction | None:
        for f in self.files:
            if f.path == path:
                return f.action
        return None

    def paths_for(self, action: PlanAction) -> tuple[str, ...]:
        return tuple(f.path for f in self.files if f.action == action)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.files:
            out[str(f.action)] = out.get(str(f.action), 0) + 1
        return dict(sorted(out.items()))

    def to_dict(self) -> dict[str, object]:
        ordered = sorted(self.files, key=lambda f: f.path)
        return {
            "incremental": self.incremental,
            "counts": self.counts(),
            "files": [
                {"path": f.path, "action": str(f.action), "reason": f.reason}
                for f in ordered
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


_TS_EXTS = (".ts", ".tsx", ".d.ts", ".mts", ".cts", "/index.ts", "/index.tsx")


def _candidate_paths(importer: str, text: str) -> set[str]:
    """Caminhos de módulo que o texto de import poderia designar.

    Cobre python absoluto/relativo e TS relativo, com todos os prefixos
    (``pkg.util.helper`` pode ser satisfeito por ``pkg/util.py``).
    """
    cands: set[str] = set()

    def py_prefixed(dotted: str) -> None:
        segs = [s for s in dotted.split(".") if s]
        for i in range(len(segs), 0, -1):
            base = "/".join(segs[:i])
            cands.add(base + ".py")
            cands.add(base + "/__init__.py")

    if text.startswith(("./", "../")):
        base = posixpath.normpath(
            posixpath.join(posixpath.dirname(importer), text)
        )
        for ext in _TS_EXTS:
            cands.add(base + ext)
        cands.add(base)
        # `./util.member` — último segmento pode ser membro, não path
        parent, _, _leaf = base.rpartition("/")
        if parent:
            for ext in _TS_EXTS:
                cands.add(parent + ext)
    elif text.startswith("."):
        dots = len(text) - len(text.lstrip("."))
        rest = text.lstrip(".")
        dir_parts = importer.split("/")[:-1]
        keep = dir_parts[: max(len(dir_parts) - (dots - 1), 0)]
        if rest:
            py_prefixed(".".join([*keep, *rest.split(".")]))
        else:
            py_prefixed(".".join(keep))
    else:
        py_prefixed(text)
    return cands


class InvalidationPlanner:
    """Gera ``IndexPlan`` a partir do ChangeSet e do grafo anterior."""

    def plan(
        self,
        changeset: ChangeSet,
        prior_import_edges: list[tuple[str, str | None, str, str]],
        prior_parse_status: dict[str, str] | None = None,
    ) -> IndexPlan:
        """``prior_import_edges``: linhas de ``Store.module_import_edges``
        (importer, imported_path|None, to_text, resolution_status).
        ``prior_parse_status``: path → parse_status do snapshot anterior;
        arquivos com parse incompleto têm dependências desconhecidas.
        """
        actions, affected = self._primary_actions(changeset)
        self._propagate_dependents(changeset, prior_import_edges,
                                   actions, affected)
        self._revalidate_unresolved(changeset, prior_import_edges,
                                    actions, affected)
        self._revalidate_incomplete(changeset, prior_parse_status or {},
                                    actions, affected)
        self._revalidate_resolver_bump(changeset, actions)
        return IndexPlan(
            files=tuple(sorted(actions.values(), key=lambda f: f.path))
        )

    def _primary_actions(
        self, changeset: ChangeSet
    ) -> tuple[dict[str, PlannedFile], set[str]]:
        """1. ações primárias: DROP/REUSE/REPARSE + conjunto afetado."""
        actions: dict[str, PlannedFile] = {}
        affected: set[str] = set()
        for c in changeset.changes:
            if c.kind == ChangeKind.DELETED:
                actions[c.path] = PlannedFile(
                    c.path, PlanAction.DROP, f"deleted: {c.reason}"
                )
                affected.add(c.path)
            elif c.kind == ChangeKind.UNCHANGED:
                actions[c.path] = PlannedFile(
                    c.path, PlanAction.REUSE, "unchanged and unaffected"
                )
            else:
                actions[c.path] = PlannedFile(
                    c.path, PlanAction.REPARSE, f"{c.kind}: {c.reason}"
                )
                affected.add(c.path)
        return actions, affected

    def _propagate_dependents(
        self,
        changeset: ChangeSet,
        prior_import_edges: list[tuple[str, str | None, str, str]],
        actions: dict[str, PlannedFile],
        affected: set[str],
    ) -> None:
        """2. fechamento transitivo de dependentes via arestas IMPORTS
        resolvidas — cobre cadeias de re-export e ciclos (visitados)."""
        del changeset
        imported_to_importers: dict[str, set[str]] = {}
        for importer, imported, _text, _status in prior_import_edges:
            if imported:
                imported_to_importers.setdefault(imported, set()).add(importer)

        frontier = set(affected)
        seen = set(affected)
        while frontier:
            target = frontier.pop()
            for importer in sorted(imported_to_importers.get(target, ())):
                if importer in seen:
                    continue
                seen.add(importer)
                cur = actions.get(importer)
                if cur is not None and cur.action in (
                    PlanAction.REPARSE, PlanAction.DROP,
                ):
                    continue  # já coberto por ação mais forte
                actions[importer] = PlannedFile(
                    importer, PlanAction.REVALIDATE_RELATIONS,
                    f"imports changed module {target}",
                )
                frontier.add(importer)

    def _revalidate_unresolved(
        self,
        changeset: ChangeSet,
        prior_import_edges: list[tuple[str, str | None, str, str]],
        actions: dict[str, PlannedFile],
        affected: set[str],
    ) -> None:
        """3. imports sem alvo persistido: texto → candidatos; mudança
        num candidato pode mudar o resultado da resolução."""
        del changeset
        for importer, imported, text, _status in prior_import_edges:
            if imported or not text:
                continue
            hits = _candidate_paths(importer, text) & affected
            if hits:
                hit = sorted(hits)[0]
                cur = actions.get(importer)
                if cur is None or cur.action == PlanAction.REUSE:
                    actions[importer] = PlannedFile(
                        importer, PlanAction.REVALIDATE_RELATIONS,
                        f"unresolved import '{text}' may now match {hit}",
                    )

    def _revalidate_incomplete(
        self,
        changeset: ChangeSet,
        prior_status: dict[str, str],
        actions: dict[str, PlannedFile],
        affected: set[str],
    ) -> None:
        """4. dependências incompletas → conservador: parse parcial/falho
        no snapshot anterior é reavaliado quando existe mudança real."""
        if not affected:
            return
        for c in changeset.changes:
            if c.kind != ChangeKind.UNCHANGED:
                continue
            st = prior_status.get(c.path)
            if st in ("parse-failed", "parsed-with-errors", "unparsed"):
                actions[c.path] = PlannedFile(
                    c.path, PlanAction.REVALIDATE_RELATIONS,
                    f"incomplete dependency info (previous {st})",
                )

    def _revalidate_resolver_bump(
        self, changeset: ChangeSet, actions: dict[str, PlannedFile]
    ) -> None:
        """5. bump de resolver → reavalia relações dos reaproveitados."""
        if not changeset.resolver_changes:
            return
        for c in changeset.changes:
            cur = actions.get(c.path)
            if cur is not None and cur.action == PlanAction.REUSE:
                actions[c.path] = PlannedFile(
                    c.path, PlanAction.REVALIDATE_RELATIONS,
                    "resolver version changed "
                    f"({', '.join(changeset.resolver_changes)})",
                )
