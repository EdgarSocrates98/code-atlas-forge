"""Gaps semânticos do Cycle 3.1 (lf-0033) — desfechos honestos pinados.

Cada caso foi medido no pipeline real antes de ser pinado: o assert
reproduz o outcome observado, nunca um outcome desejado.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "resolvers"))

from eval_harness import snapshot_sets


def _rels(db_path: Path, repo: Path) -> set:
    _s, rels, _f, _sid = snapshot_sets(db_path, repo)
    return rels


def _write_pkg(root: Path) -> Path:
    pkg = root / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "base.py").write_text(
        'class Animal:\n    def speak(self) -> str:\n        return "?"\n'
    )
    (pkg / "dog.py").write_text(
        "from pkg.base import Animal\n\n"
        "class Dog(Animal):\n    pass\n\n"
        "def run() -> str:\n    d = Dog()\n    return d.speak()\n"
    )
    return root


def test_inheritance_extends_resolves_to_bound_base(tmp_path: Path) -> None:
    """`class Dog(Animal)` com `from pkg.base import Animal` — EXTENDS
    resolve para o símbolo base importado (binding real)."""
    repo = _write_pkg(tmp_path / "inherit")
    rels = _rels(tmp_path / "i.db", repo)
    assert (
        "EXTENDS", "pkg.dog.Dog", "pkg.base.Animal", "resolved", "resolved"
    ) in rels


def test_instance_method_call_honest_unresolved(tmp_path: Path) -> None:
    """`d.speak()` — resolver sintático não infere tipo do receiver;
    desfecho honesto é unresolved, nunca resolved ao alvo errado."""
    repo = _write_pkg(tmp_path / "inst")
    rels = _rels(tmp_path / "m.db", repo)
    edge = next(
        r for r in rels if r[0] == "CALLS" and "speak" in r[2]
    )
    assert edge[3] == "unresolved", edge
    assert not any(
        r[0] == "CALLS" and "speak" in r[2] and r[3] == "resolved"
        for r in rels
    )


def test_dynamic_import_python_no_invented_edge(tmp_path: Path) -> None:
    """`importlib.import_module("pkg.base")` é dinâmico — nenhuma aresta
    IMPORTS inventada; call ao importlib é external; `m.Animal()`
    unresolved."""
    repo = _write_pkg(tmp_path / "dynpy")
    (repo / "pkg" / "dyn.py").write_text(
        "import importlib\n\n"
        "def load():\n    m = importlib.import_module('pkg.base')\n"
        "    return m.Animal()\n"
    )
    rels = _rels(tmp_path / "d.db", repo)
    assert not any(
        r[0] == "IMPORTS" and r[1] == "pkg.dyn" and "base" in r[2]
        for r in rels
    )
    assert any(
        r[0] == "IMPORTS" and r[1] == "pkg.dyn" and "importlib" in r[2]
        and r[3] == "external"
        for r in rels
    )
    member = next(r for r in rels if r[0] == "CALLS" and "Animal" in r[2])
    assert member[3] == "unresolved", member


def test_dynamic_import_ts_no_invented_edge(tmp_path: Path) -> None:
    """TS `await import("./mod")` com literal é dependência real —
    IMPORTS extraída e resolvida (lf-0130). `import(expr)` não-literal
    continua sem aresta inventada (diagnóstico honesto, nenhum CALLS
    espúrio para `import`)."""
    repo = tmp_path / "dynts"
    repo.mkdir()
    (repo / "mod.ts").write_text("export function f(): number {\n  return 1;\n}\n")
    (repo / "use.ts").write_text(
        'export async function go(name: string): Promise<number> {\n'
        '  const m = await import("./mod");\n'
        '  const v = await import(name);\n  return 1;\n}\n'
    )
    rels = _rels(tmp_path / "t.db", repo)
    assert any(
        r[0] == "IMPORTS" and r[1] == "use" and r[2] == "mod"
        and r[3] == "resolved"
        for r in rels
    )
    assert not any(
        r[0] == "CALLS" and "import" in r[2]
        for r in rels
    )


def test_ts_interface_method_honest(tmp_path: Path) -> None:
    """`s.area()` em instância de `ShapeImpl` (implements Shape) —
    syntactic não segue interface; asserta que nunca resolve errado."""
    from eval_harness import CORPUS_DIR

    rels = _rels(tmp_path / "ts.db", CORPUS_DIR / "ts_advanced")
    assert not any(
        r[0] == "CALLS" and r[1] == "src.use.useAll"
        and "area" in r[2] and r[3] == "resolved" and "iface" not in r[2]
        for r in rels
    )
