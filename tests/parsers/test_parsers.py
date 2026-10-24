"""Testes do subsistema de parsing — árvores e símbolos reais."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from code_atlas.parsers import ParseEngine, ParseLimits, ParserRegistry
from code_atlas.parsers.languages.python import PythonAdapter
from code_atlas.parsers.languages.tsx import TsxAdapter
from code_atlas.parsers.languages.typescript import TypeScriptAdapter

PY_SRC = '''"""Docstring do módulo."""
import os
import a.b as c
from m import q as w, r

@dec
def d(): pass

async def af(x: int = 1) -> int:
    """doc af"""
    def inner(): return x
    return inner()

class K(B1, B2):
    """doc K"""
    def m(self): pass
    async def am(self): pass
'''.encode()

TS_SRC = b'''import {a as b} from "m";
import * as ns from "n";
export interface I extends J {}
export class C extends B implements I, K {
  async m() {}
  constructor() {}
}
const f = async (x: number) => x;
type T = string;
enum E { A }
export default function g() {}
'''

TSX_SRC = b'''export const Card = (p: {t: string}) => <div title={p.t}>x</div>;
const el = <Card t="hi"/>;
'''


@pytest.fixture()
def engine() -> ParseEngine:
    return ParseEngine()


# -- real parse -------------------------------------------------------


def test_python_real_parse(engine: ParseEngine) -> None:
    res = engine.parse("pkg/mod.py", PY_SRC)
    assert res.supported and not res.has_error
    assert res.language == "python"
    assert res.parser == "tree-sitter-python"
    assert res.parser_version and res.grammar_version
    names = {s.qualified_name: s for s in res.symbols}
    assert "pkg.mod" in names and names["pkg.mod"].kind == "module"
    assert names["pkg.mod"].docstring == "Docstring do módulo."
    assert names["pkg.mod.K"].kind == "class"
    assert set(names["pkg.mod.K"].bases) == {"B1", "B2"}
    assert names["pkg.mod.K.m"].kind == "method"
    assert names["pkg.mod.K.am"].kind == "method"
    assert "async" in names["pkg.mod.K.am"].modifiers
    assert names["pkg.mod.af"].kind == "function"
    assert "async" in names["pkg.mod.af"].modifiers
    assert names["pkg.mod.af"].signature.startswith("async def af")
    assert "inner" in names["pkg.mod.af.inner"].name
    assert names["pkg.mod.af.inner"].scope_path == ("af",)
    assert names["pkg.mod.d"].decorators == ("dec",)


def test_python_imports_and_calls(engine: ParseEngine) -> None:
    res = engine.parse("pkg/mod.py", PY_SRC)
    imports = [r for r in res.relations if r.type == "IMPORTS"]
    targets = {r.to_name for r in imports}
    assert {"os", "a.b", "m.q", "m.r"} <= targets
    extends = [r for r in res.relations if r.type == "EXTENDS"]
    assert {r.to_name for r in extends} == {"B1", "B2"}


def test_typescript_real_parse(engine: ParseEngine) -> None:
    res = engine.parse("src/app.ts", TS_SRC)
    assert res.supported and not res.has_error
    names = {s.qualified_name: s for s in res.symbols}
    assert names["src.app.I"].kind == "interface"
    assert names["src.app.I"].bases == ("J",)
    c = names["src.app.C"]
    assert c.kind == "class"
    assert c.bases == ("B",)
    assert set(c.implements) == {"I", "K"}
    assert names["src.app.C.m"].kind == "method"
    assert "async" in names["src.app.C.m"].modifiers
    assert names["src.app.C.constructor"].kind == "method"
    f = names["src.app.f"]
    assert f.kind == "function"  # arrow nomeada
    assert "async" in f.modifiers
    assert names["src.app.T"].kind == "symbol"  # type alias
    assert names["src.app.E"].kind == "symbol"  # enum
    assert names["src.app.g"].kind == "function"
    assert "export" in names["src.app.g"].modifiers
    impls = [r for r in res.relations if r.type == "IMPLEMENTS"]
    assert {r.to_name for r in impls} == {"I", "K"}


def test_tsx_jsx_real_parse(engine: ParseEngine) -> None:
    res = engine.parse("ui/Card.tsx", TSX_SRC)
    assert res.supported and not res.has_error
    assert res.language == "tsx"
    names = {s.qualified_name: s for s in res.symbols}
    assert "ui.Card.Card" in names
    jsx = [r for r in res.references if r.context == "jsx"]
    assert any(r.name == "Card" for r in jsx)


# -- resiliência -------------------------------------------------------


def test_broken_python_diagnostics_not_crash(engine: ParseEngine) -> None:
    res = engine.parse("bad.py", b"def broken(:\n    pass\n")
    assert res.has_error
    assert res.error_node_count + res.missing_node_count > 0
    assert any(d.code in ("error-node", "missing-node") for d in res.diagnostics)


def test_unsupported_language(engine: ParseEngine) -> None:
    res = engine.parse("readme.md", b"# hi")
    assert not res.supported
    assert res.diagnostics[0].code == "unsupported-language"


def test_file_too_large(engine: ParseEngine) -> None:
    eng = ParseEngine(limits=ParseLimits(max_file_size=10))
    res = eng.parse("big.py", PY_SRC)
    assert not res.supported
    assert res.diagnostics[0].code == "file-too-large"


def test_batch_isolation(engine: ParseEngine) -> None:
    results = engine.parse_batch(
        [("ok.py", b"x = 1\n"), ("bad.py", b"def (:\n"), ("ok2.py", b"y = 2\n")]
    )
    assert len(results) == 3
    assert results[0].supported and not results[0].has_error
    assert results[1].has_error
    assert results[2].supported and not results[2].has_error


def test_empty_and_unicode(engine: ParseEngine) -> None:
    assert engine.parse("e.py", b"").supported
    res = engine.parse("u.py", "def função_é(): return '日本語'\n".encode())
    assert any(s.name == "função_é" for s in res.symbols)


def test_determinism(engine: ParseEngine) -> None:
    r1 = engine.parse("a/b.py", PY_SRC)
    r2 = ParseEngine().parse("a/b.py", PY_SRC)
    assert r1.symbols == r2.symbols
    assert r1.relations == r2.relations
    assert r1.content_hash == r2.content_hash


def test_registry_lookup() -> None:
    reg = ParserRegistry.default()
    assert set(reg.languages) == {"python", "tsx", "typescript"}
    assert isinstance(reg.for_path("a/b.py"), PythonAdapter)
    assert isinstance(reg.for_path("a/b.ts"), TypeScriptAdapter)
    assert isinstance(reg.for_path("a/b.tsx"), TsxAdapter)
    assert reg.for_path("a/b.md") is None


# -- fronteira arquitetural --------------------------------------------

SRC = Path(__file__).resolve().parents[2] / "src" / "code_atlas"


def test_no_tree_sitter_outside_parsers() -> None:
    """tree_sitter só pode ser importado dentro de parsers/ (boundary)."""
    offenders = []
    for py in SRC.rglob("*.py"):
        if "parsers" in py.parts:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders += [
                    f"{py}:{node.lineno}"
                    for a in node.names
                    if a.name.startswith("tree_sitter")
                ]
            elif isinstance(node, ast.ImportFrom) and (
                node.module or ""
            ).startswith("tree_sitter"):
                offenders.append(f"{py}:{node.lineno}")
    assert offenders == []
