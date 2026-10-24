"""Fixtures semânticos/adversariais do Cycle 3 (lf-0024).

Regra do grill: resolver nunca rotula link plausível-mas-falso como
resolved — cada caso asserta o desfecho específico observado no banco
real, não o manifest agregado.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "resolvers"))

from eval_harness import CORPUS_DIR, evaluate_incremental, snapshot_sets
from scip_fixture import (  # type: ignore[import-not-found]
    index_bytes,
    tool_info,
)

from code_atlas.resolvers.lsp.spawn import (
    LspServerSpec,
    SpawnError,
    spawn_server,
)
from code_atlas.resolvers.scip.loader import load_index


def valid_index() -> bytes:
    return index_bytes("file:///repo", [], tool=tool_info())


def _rels(db_path: Path, repo: Path) -> set:
    _s, rels, _f, _sid = snapshot_sets(db_path, repo)
    return rels


# --- adversariais: nunca claim resolved em link falso -----------------


def test_shadowed_import_never_resolves_to_imported(tmp_path: Path) -> None:
    """`def helper()` local sombreia `from pkg.util import helper` —
    sem ordem textual o desfecho honesto é ambiguous."""
    rels = _rels(tmp_path / "a.db", CORPUS_DIR / "py_advanced")
    edge = next(
        r for r in rels if r[0] == "CALLS" and r[1] == "pkg.shadowed.use"
        and "helper" in r[2]
    )
    assert edge[3] == "ambiguous", edge
    # nunca resolved para o alvo plausível-mas-errado
    assert not any(
        r[0] == "CALLS" and r[1] == "pkg.shadowed.use"
        and r[2] == "pkg.util.helper" and r[3] == "resolved"
        for r in rels
    )


def test_homonymous_picks_bound_import_not_sibling(tmp_path: Path) -> None:
    """`process` importado de dup_a resolve para dup_a — nunca dup_b."""
    rels = _rels(tmp_path / "b.db", CORPUS_DIR / "py_advanced")
    assert (
        "CALLS", "main.main", "pkg.dup_a.process", "resolved", "resolved"
    ) in rels
    assert not any(
        r[0] == "CALLS" and r[1] == "main.main" and "dup_b" in r[2]
        for r in rels
    )


def test_star_import_gives_module_edge_not_member(tmp_path: Path) -> None:
    """`from pkg.star_target import *` — aresta de módulo resolved;
    o membro `star_fn` NÃO é resolvido (sem binding real)."""
    rels = _rels(tmp_path / "c.db", CORPUS_DIR / "py_advanced")
    assert (
        "IMPORTS", "pkg.star_user", "pkg.star_target",
        "resolved", "resolved",
    ) in rels
    assert not any(
        r[0] == "CALLS" and r[1] == "pkg.star_user.use_star"
        and r[3] == "resolved"
        for r in rels
    )


def test_barrel_member_calls_stay_honest(tmp_path: Path) -> None:
    """TS barrel: import do index resolve ao módulo, mas membros
    reexportados (`Thing`, `otherFn`) não viram resolved por coincidência
    de nome — resolver sintático não segue `export { X }`."""
    rels = _rels(tmp_path / "d.db", CORPUS_DIR / "ts_advanced")
    assert (
        "IMPORTS", "src.use", "src.barrel.index", "resolved", "resolved"
    ) in rels
    for text in ("Thing", "otherFn"):
        assert not any(
            r[0] == "CALLS" and r[1] == "src.use.useAll"
            and r[2] == f"src.barrel.index.{text}" and r[3] == "resolved"
            for r in rels
        ), text


def test_circular_imports_resolve_both_ways(tmp_path: Path) -> None:
    """Ciclo a↔b: ambas arestas IMPORTS resolvidas, calls nos dois lados."""
    rels = _rels(tmp_path / "e.db", CORPUS_DIR / "py_advanced")
    assert (
        "IMPORTS", "pkg.cycle_a", "pkg.cycle_b", "resolved", "resolved"
    ) in rels
    assert (
        "IMPORTS", "pkg.cycle_b", "pkg.cycle_a", "resolved", "resolved"
    ) in rels
    assert (
        "CALLS", "pkg.cycle_a.alpha", "pkg.cycle_b.beta",
        "resolved", "resolved",
    ) in rels


def test_relative_import_forms_resolve(tmp_path: Path) -> None:
    """`from . import util` e `from .util import helper` — ambos
    resolvem para pkg.util (regressão do separador `..`)."""
    rels = _rels(tmp_path / "f.db", CORPUS_DIR / "py_advanced")
    assert (
        "IMPORTS", "pkg.rel", "pkg.util", "resolved", "resolved"
    ) in rels
    assert not any(
        r[0] == "IMPORTS" and r[1] == "pkg.rel" and r[3] != "resolved"
        for r in rels
    )


# --- incremental-state fixtures ---------------------------------------


def _copy_corpus(dst: Path, name: str) -> Path:
    repo = dst / name
    shutil.copytree(CORPUS_DIR / "py_advanced", repo)
    return repo


def test_invalidation_single_file_change(tmp_path: Path) -> None:
    repo = _copy_corpus(tmp_path, "inc1")
    target = repo / "pkg" / "dup_a.py"
    old = target.read_text()

    res = evaluate_incremental(
        repo, tmp_path / "i.db",
        mutate=lambda: target.write_text(old + "\n\ndef added():\n    return 1\n"),
        restore=lambda: target.write_text(old),
    )
    assert res["incremental_matches_full"]
    assert res["reuse_symbol_ids_stable"]
    assert res["plan_actions"].get("pkg/dup_a.py") == "reparse"
    assert "pkg/core.py" in {
        p for p, a in res["plan_actions"].items()
        if a == "revalidate-relations"
    }


def test_invalidation_rename_pair(tmp_path: Path) -> None:
    repo = _copy_corpus(tmp_path, "inc2")
    src, dst = repo / "pkg" / "dup_a.py", repo / "pkg" / "renamed.py"

    res = evaluate_incremental(
        repo, tmp_path / "r.db",
        mutate=lambda: src.rename(dst),
        restore=lambda: dst.rename(src),
    )
    assert res["incremental_matches_full"]
    assert res["plan_actions"].get("pkg/dup_a.py") == "drop"
    assert res["plan_actions"].get("pkg/renamed.py") == "reparse"


def test_invalidation_delete(tmp_path: Path) -> None:
    repo = _copy_corpus(tmp_path, "inc3")
    target = repo / "pkg" / "dup_b.py"
    old = target.read_bytes()

    res = evaluate_incremental(
        repo, tmp_path / "d.db",
        mutate=target.unlink,
        restore=lambda: target.write_bytes(old),
    )
    assert res["incremental_matches_full"]
    assert res["plan_actions"].get("pkg/dup_b.py") == "drop"


# --- scip_bad: rejeição de índice externo incompatível ----------------


def test_scip_bad_fixtures_rejected(tmp_path: Path) -> None:
    """Corrupt / truncado / wrong-repo / oversize — nunca crash nem
    associação silenciosa."""
    # corrupt: bytes truncados no meio do frame
    bad = tmp_path / "truncated.scip"
    bad.write_bytes(valid_index()[: len(valid_index()) // 2])
    out = load_index(bad)
    assert not out.ok and out.errors

    # garbage total
    garbage = tmp_path / "garbage.scip"
    garbage.write_bytes(b"\xff\xfe\x00\x01not-protobuf")
    out = load_index(garbage)
    assert not out.ok and out.errors

    # wrong repo root — binding de revisão rejeita por default
    wrong = tmp_path / "wrong_root.scip"
    wrong.write_bytes(
        index_bytes("file:///other/repo", [], tool=tool_info())
    )
    workspace = tmp_path / "ws"
    workspace.mkdir()
    out = load_index(wrong, workspace_root=workspace)
    assert not out.ok and any("project_root" in e for e in out.errors)

    # oversize — cap honrado antes de decodificar
    big = tmp_path / "big.scip"
    big.write_bytes(valid_index())
    out = load_index(big, max_bytes=4)
    assert not out.ok and any("too large" in e for e in out.errors)


# --- lsp_absent: binário inexistente degrada com erro tipado ----------


def test_lsp_absent_typed_error_not_crash() -> None:
    spec = LspServerSpec(
        name="nonexistent-lsp",
        cmd=("definitely-not-a-real-lsp-binary-xyz", "--stdio"),
        languages=frozenset({"python"}),
        enabled=True,
    )
    with pytest.raises(SpawnError, match="not found"):
        spawn_server(spec)


def test_lsp_disabled_by_default() -> None:
    """Opt-in é o default — nada spawn sem `enabled=True` explícito."""
    spec = LspServerSpec(
        name="x", cmd=("whatever",), languages=frozenset()
    )
    assert spec.enabled is False
