"""Cobertura Phase 3 — extração estrutural de símbolos.

Gate 3 do prompt: fixtures com símbolos aninhados, aliases, overloads,
decorators e assinaturas complexas. Política documentada: funções
anônimas só ganham identidade quando ligadas a um nome.
"""

from __future__ import annotations

import pytest

from code_atlas.parsers import ParseEngine


@pytest.fixture()
def engine() -> ParseEngine:
    return ParseEngine()


def _by_qname(res: object) -> dict[str, object]:
    return {s.qualified_name: s for s in res.symbols}  # type: ignore[attr-defined]


# -- Python: cobertura do vocabulário -----------------------------------


def test_py_nested_and_scope_collision(engine: ParseEngine) -> None:
    src = b"""
def outer():
    def helper(): pass

class A:
    def helper(self): pass

class B:
    def helper(self): pass
"""
    names = _by_qname(engine.parse("m.py", src))
    assert names["m.outer.helper"].scope_path == ("outer",)
    assert names["m.A.helper"].qualified_name != names["m.B.helper"].qualified_name
    assert {names["m.A.helper"].kind, names["m.B.helper"].kind} == {"method"}


def test_py_overload_decorators_params(engine: ParseEngine) -> None:
    src = b'''from typing import overload

class S:
    @overload
    def get(self, k: str) -> int: ...
    @overload
    def get(self, k: bytes) -> str: ...
    @staticmethod
    def build(*args, **kw): pass
'''
    names = _by_qname(engine.parse("svc.py", src))
    # duas assinaturas de overload — mesmo qname, spans distintos
    overloads = [s for s in engine.parse("svc.py", src).symbols
                 if s.qualified_name == "svc.S.get"]
    assert len(overloads) == 2
    assert overloads[0].span.start_line != overloads[1].span.start_line
    assert "overload" in overloads[0].modifiers or "overload" in overloads[0].decorators
    build = names["svc.S.build"]
    assert "staticmethod" in build.modifiers
    assert set(build.params) == {"args", "kw"}


def test_py_import_aliases(engine: ParseEngine) -> None:
    src = b"import numpy as np\nfrom os.path import join as j, exists\n"
    res = engine.parse("m.py", src)
    imp_names = {r.name for r in res.references if r.context == "import"}
    assert {"numpy", "os.path.join", "os.path.exists"} <= imp_names


def test_py_unicode_identifiers(engine: ParseEngine) -> None:
    src = "def ação_総合(x): return x\nclass Café: pass\n".encode()
    names = _by_qname(engine.parse("u.py", src))
    assert "u.ação_総合" in names and "u.Café" in names


# -- TypeScript / TSX ----------------------------------------------------


def test_ts_arrow_named_vs_anonymous(engine: ParseEngine) -> None:
    src = b"""const f = (x: number) => x * 2;
[1].map((n) => n + 1);  // anonima - sem identidade
function top() {}
"""
    names = _by_qname(engine.parse("a.ts", src))
    assert names["a.f"].kind == "function"
    assert names["a.top"].kind == "function"
    assert len([s for s in names.values() if s.kind == "function"]) == 2
    # nenhum símbolo para a arrow anônima dentro de .map()


def test_ts_type_declarations_and_interface(engine: ParseEngine) -> None:
    src = b"""interface Shape extends Base { area(): number; }
type ID = string | number;
enum Color { Red }
declare function amb(x: string): void;
"""
    names = _by_qname(engine.parse("t.ts", src))
    assert names["t.Shape"].kind == "interface"
    assert names["t.Shape"].bases == ("Base",)
    assert names["t.ID"].kind == "symbol"
    assert names["t.Color"].kind == "symbol"
    assert "signature-only" in names["t.amb"].modifiers


def test_ts_implements_and_ctor(engine: ParseEngine) -> None:
    src = b"class C extends B implements I { constructor() {} async run() {} }"
    res = engine.parse("c.ts", src)
    names = _by_qname(res)
    assert names["c.C"].implements == ("I",)
    assert names["c.C.constructor"].kind == "method"
    assert "async" in names["c.C.run"].modifiers


def test_tsx_component(engine: ParseEngine) -> None:
    src = b"""interface Props { title: string }
export function Card(p: Props) { return <div>{p.title}</div> }
export const Btn = (p: Props) => <button>{p.title}</button>
"""
    res = engine.parse("ui.tsx", src)
    names = _by_qname(res)
    assert names["ui.Card"].kind == "function"
    assert "export" in names["ui.Card"].modifiers
    assert names["ui.Btn"].kind == "function"  # arrow nomeada
    assert names["ui.Props"].kind == "interface"


# -- política de identidade ----------------------------------------------


def test_identity_ignores_line_numbers(engine: ParseEngine) -> None:
    a = engine.parse("m.py", b"def f(): pass\n")
    b = engine.parse("m.py", b"\n\n\ndef f(): pass\n")  # mesma função, outra linha
    sa = next(s for s in a.symbols if s.name == "f")
    sb = next(s for s in b.symbols if s.name == "f")
    assert sa.qualified_name == sb.qualified_name  # linha não é identidade


def test_extraction_deterministic(engine: ParseEngine) -> None:
    src = b"class K: pass\ndef f(): pass\n"
    r1 = engine.parse("m.py", src)
    r2 = ParseEngine().parse("m.py", src)
    assert [s.qualified_name for s in r1.symbols] == [
        s.qualified_name for s in r2.symbols
    ]
