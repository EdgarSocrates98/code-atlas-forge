"""lf-0130 — compatibilidade de dialetos TS/TSX (Cycle 8 Phase 3).

Cobre os constructs que faltavam na extração TypeScript, descobertos no
audit sobre código real (axios CJS helpers, barrels, .d.cts):
``import x = require``, ``import foo = Bar.Baz``, ``export =``,
re-exports, ``import()`` dinâmico, ``require()`` CJS, namespaces,
decorators, ``export declare`` e declaradores sem inicializador.
"""

from __future__ import annotations

import pytest

from code_atlas.parsers import ParseEngine


@pytest.fixture()
def engine() -> ParseEngine:
    return ParseEngine()


def _imports(res):
    return [r for r in res.relations if r.type == "IMPORTS"]


def _bindings(res):
    return {r.name: r.target for r in res.references
            if r.context == "import-member"}


# -- import x = require (P1 — axios cjs helpers) ------------------------


def test_import_equals_require_module_path(engine: ParseEngine) -> None:
    """`import x = require('m')`: source mora no import_require_clause —
    IMPORTS aponta para 'm' e o binding `x` liga ao módulo inteiro."""
    res = engine.parse("h/cjs.ts", b"import axios = require('axios');\n")
    assert res.supported and not res.has_error
    imports = _imports(res)
    assert [r.to_name for r in imports] == ["axios"]
    assert _bindings(res)["axios"] == "axios"


def test_import_alias_entity_no_module_edge(engine: ParseEngine) -> None:
    """`import foo = Bar.Baz` (import_alias) — alias de entidade, não de
    módulo: binding registrado, nenhuma aresta IMPORTS."""
    res = engine.parse("h/a.ts", b"import foo = Bar.Baz;\n")
    assert _imports(res) == []
    assert _bindings(res)["foo"] == "Bar.Baz"


def test_import_member_never_empty(engine: ParseEngine) -> None:
    """Invariante: nenhum IMPORTS com to_name vazio sai do extractor —
    era o bug que derrubava o snapshot inteiro (validate_graph)."""
    src = b"""import a = require('a');
import b = require('b/c');
import {x} from './x';
import './side';
"""
    res = engine.parse("h/all.ts", src)
    for r in _imports(res):
        assert r.to_name.strip()


# -- exports -------------------------------------------------------------


def test_export_equals_assignment_reference(engine: ParseEngine) -> None:
    """`export = axios` (CJS/UMD): a expressão vira referência
    export-assignment — o módulo inteiro é o export."""
    res = engine.parse(
        "index.d.cts",
        b"declare const axios: number;\nexport = axios;\n")
    refs = [r for r in res.references if r.context == "export-assignment"]
    assert [r.name for r in refs] == ["axios"]
    # `declare const` também vira símbolo (declarador sem valor)
    assert any(s.name == "axios" and s.kind == "symbol"
               for s in res.symbols)


def test_export_from_creates_imports_and_bindings(
    engine: ParseEngine,
) -> None:
    """Re-export é dependência real: IMPORTS + bindings dos nomes."""
    res = engine.parse(
        "b/index.ts",
        b"export {T} from './t';\n"
        b"export {a as c} from './u';\n"
        b"export * from './all';\n"
        b"export * as ns from './ns';\n",
    )
    targets = {r.to_name for r in _imports(res)}
    assert targets == {"./t", "./u", "./all", "./ns"}
    b = _bindings(res)
    assert b["T"] == "./t.T"
    assert b["c"] == "./u.a"
    assert b["*"] == "./all.*"
    assert b["ns"] == "./ns.*"


def test_export_local_specifiers_are_references(
    engine: ParseEngine,
) -> None:
    res = engine.parse("b/l.ts", b"const x = 1;\nexport {x};\n")
    refs = [r for r in res.references if r.context == "export"]
    assert [r.name for r in refs] == ["x"]


def test_export_default_anonymous(engine: ParseEngine) -> None:
    res = engine.parse(
        "b/d.ts",
        b"export default function () { return 1 }\n"
        b"export default class {}\n",
    )
    defaults = [s for s in res.symbols if s.name == "default"]
    assert {s.kind for s in defaults} == {"function", "class"}
    assert all("export" in s.modifiers for s in defaults)


def test_export_default_named_keeps_name(engine: ParseEngine) -> None:
    res = engine.parse("b/n.ts", b"export default function g() {}\n")
    names = {s.name: s for s in res.symbols}
    assert names["g"].kind == "function"
    assert "export" in names["g"].modifiers


def test_export_declare_unwraps(engine: ParseEngine) -> None:
    res = engine.parse(
        "b/dc.ts", b"export declare class DC { m(): void }\n")
    names = {s.qualified_name: s for s in res.symbols}
    assert names["b.dc.DC"].kind == "class"
    assert "export" in names["b.dc.DC"].modifiers
    assert names["b.dc.DC.m"].kind == "method"


# -- dynamic import / require ------------------------------------------


def test_dynamic_import_literal_edge(engine: ParseEngine) -> None:
    res = engine.parse(
        "b/l.ts",
        b"export async function go() { return import('./mod'); }\n",
    )
    assert [r.to_name for r in _imports(res)] == ["./mod"]


def test_dynamic_import_nonliteral_diagnostic(
    engine: ParseEngine,
) -> None:
    res = engine.parse(
        "b/n.ts",
        b"export async function go(n: string) { return import(n); }\n",
    )
    assert _imports(res) == []
    assert any(d.code == "dynamic-import-nonliteral"
               for d in res.diagnostics)


def test_require_call_and_binding(engine: ParseEngine) -> None:
    res = engine.parse(
        "h/r.ts",
        b"const assert = require('assert');\n"
        b"const axios = require('axios').default;\n"
        b"require('side');\n",
    )
    assert {r.to_name for r in _imports(res)} == {
        "assert", "axios", "side"}
    b = _bindings(res)
    assert b["assert"] == "assert"
    assert b["axios"] == "axios.default"
    # require literal vira IMPORTS, não CALLS espúrio
    calls = [r for r in res.relations if r.type == "CALLS"]
    assert all(r.to_name != "require" for r in calls)


# -- namespaces / ambient / dts ----------------------------------------


def test_namespace_scopes_children(engine: ParseEngine) -> None:
    res = engine.parse(
        "n.ts",
        b"export namespace Config {\n"
        b"  export function read(): string { return 'x' }\n"
        b"}\n",
    )
    names = {s.qualified_name: s for s in res.symbols}
    assert names["n.Config"].kind == "symbol"
    assert names["n.Config.read"].kind == "function"
    assert names["n.Config.read"].scope_path == ("Config",)


def test_declare_namespace_and_module(engine: ParseEngine) -> None:
    res = engine.parse(
        "t.d.ts",
        b"declare namespace axios { function f(): void }\n"
        b"declare module 'ext' { export function g(): void }\n",
    )
    names = {s.qualified_name: s for s in res.symbols}
    assert names["t.axios.f"].kind == "function"
    assert names["t.ext.g"].kind == "function"


def test_module_name_dcts_dmts() -> None:
    from code_atlas.parsers.languages.typescript import module_name
    assert module_name("index.d.cts") == "index"
    assert module_name("index.d.mts") == "index"
    assert module_name("a/x.cts") == "a.x"
    assert module_name("a/x.mts") == "a.x"


# -- decorators ----------------------------------------------------------


def test_decorators_class_and_method(engine: ParseEngine) -> None:
    res = engine.parse(
        "d.ts",
        b"@Injectable()\nclass Svc {\n  @memo\n  get y() { return 1 }\n}\n",
    )
    names = {s.qualified_name: s for s in res.symbols}
    assert names["d.Svc"].decorators == ("Injectable()",)
    assert names["d.Svc.y"].decorators == ("memo",)
    decs = {(r.name, r.scope_path)
            for r in res.references if r.context == "decorator"}
    assert ("Injectable", ("Svc",)) in decs
    assert ("memo", ("Svc", "y")) in decs
    # `@Injectable()` não gera CALLS espúrio para Injectable
    calls = [r for r in res.relations if r.type == "CALLS"]
    assert all(r.to_name != "Injectable" for r in calls)


# -- declaradores sem inicializador -------------------------------------


def test_valueless_declarator_is_symbol(engine: ParseEngine) -> None:
    res = engine.parse(
        "v.d.ts",
        b"declare const v: number;\nexport let unbound: string;\n",
    )
    names = {s.name: s for s in res.symbols}
    assert names["v"].kind == "symbol"
    assert names["unbound"].kind == "symbol"
    assert "export" in names["unbound"].modifiers
