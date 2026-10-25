"""Adaptador TypeScript via tree-sitter-typescript — extração real.

Cobre class/interface/function/method (incl. signature-only), arrow
functions nomeáveis (bound via variable_declarator), imports/exports com
aliases, extends/implements declarados, type/enum declarations e
modificadores. A política para funções anônimas: só ganham identidade
quando ligadas a um nome (declarator/property) — documentado no spec.
"""

from __future__ import annotations

import hashlib
from typing import Any

import tree_sitter_typescript
from tree_sitter import Node

from code_atlas.parsers import tree_sitter_runtime as rt
from code_atlas.parsers.contracts import (
    ExtractedReference,
    ExtractedRelation,
    ExtractedSymbol,
    ParseDiagnostic,
    ParserCapability,
    ParseRequest,
    ParseResult,
)

_MODIFIER_TYPES = {
    "async",
    "static",
    "readonly",
    "abstract",
    "declare",
    "override_modifier",
    "accessibility_modifier",
    "default",
}


def module_name(path: str) -> str:
    """`src/a.ts` → ``src.a``; `src/b.d.ts` → ``src.b``; `i.d.cts` → ``i``."""
    for suffix in (".d.cts", ".d.mts", ".d.ts", ".tsx",
                   ".cts", ".mts", ".ts", ".jsx"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return ".".join(p for p in path.split("/") if p)


def _children_of_type(node: Node, types: tuple[str, ...]) -> list[Node]:
    return [c for c in node.named_children if c.type in types]


def _modifiers(node: Node, src: bytes) -> tuple[str, ...]:
    mods = []
    for c in node.children:
        if c.type in _MODIFIER_TYPES:
            mods.append(src[c.start_byte : c.end_byte].decode("utf-8", "replace"))
    return tuple(dict.fromkeys(mods))


class _TsExtractor:
    """Visitor compartilhado TypeScript/TSX (mesma gramática-base)."""

    def __init__(self, src: bytes, module: str, language: str) -> None:
        self.src = src
        self.module = module
        self.language = language
        self.symbols: list[ExtractedSymbol] = []
        self.refs: list[ExtractedReference] = []
        self.relations: list[ExtractedRelation] = []
        self.diagnostics: list[ParseDiagnostic] = []
        # decorators que a gramática anexa como siblings em class_body
        # (pendentes até a declaração seguinte consumi-los — lf-0130)
        self._pending_decs: list[Node] = []

    def text(self, node: Node) -> str:
        return self.src[node.start_byte : node.end_byte].decode("utf-8", "replace")

    def _diag(self, code: str, severity: str, message: str,
              node: Node) -> None:
        self.diagnostics.append(
            ParseDiagnostic(
                code=code, severity=severity, message=message,
                span=rt.span_of(node),
            )
        )

    def _emit(self, kind: str, name: str, name_node: Node, node: Node,
              scope: tuple[str, ...], **kw: Any) -> str:
        qname = ".".join((*scope, name)) if scope else name
        qname = f"{self.module}.{qname}" if self.module else qname
        self.symbols.append(
            ExtractedSymbol(
                language=self.language,
                kind=kind,
                name=name,
                qualified_name=qname,
                span=rt.span_of(node),
                name_span=rt.span_of(name_node),
                scope_path=scope,
                **kw,
            )
        )
        return qname

    def _signature(self, node: Node, body: Node | None) -> str:
        end = body.start_byte if body is not None else node.end_byte
        return self.src[node.start_byte : end].decode("utf-8", "replace").rstrip(" {\n")

    def _name_node(self, node: Node) -> Node | None:
        n = node.child_by_field_name("name")
        if n is not None:
            return n
        return next(
            (c for c in node.named_children if c.type.endswith("identifier")), None
        )

    # -- decorators ----------------------------------------------------
    # `@Injectable()` em classe é field `decorator` do próprio nó; em
    # métodos a gramática anexa o `decorator` como filho de `class_body`
    # (sibling anterior ao method_definition) — os dois caminhos cobrem.

    def _decorator_text(self, node: Node) -> str:
        return self.text(node).lstrip("@").strip()

    def _take_decorators(
        self, node: Node, scope: tuple[str, ...], name: str
    ) -> tuple[str, ...]:
        """Coleta decorators aplicados ao símbolo e emite refs
        ``decorator`` (o builder transforma em arestas REFERENCES)."""
        decs = [c for c in node.children if c.type == "decorator"]
        decs += self._pending_decs
        self._pending_decs = []
        out: list[str] = []
        for d in decs:
            text = self._decorator_text(d)
            if not text:
                continue
            out.append(text)
            ref_name = text.split("(")[0].strip()
            if ref_name:
                self.refs.append(
                    ExtractedReference(
                        name=ref_name, context="decorator",
                        span=rt.span_of(d), scope_path=(*scope, name),
                    )
                )
        return tuple(dict.fromkeys(out))

    # -- declarações --------------------------------------------------

    def _handle_class(self, node: Node, scope: tuple[str, ...],
                      exported: bool,
                      fallback_name: str | None = None) -> None:
        name_node = self._name_node(node)
        name = (
            self.text(name_node) if name_node is not None else fallback_name
        )
        if name is None:
            return
        name_node = name_node or node
        bases: list[str] = []
        implements: list[str] = []
        heritage = next(
            (c for c in node.named_children if c.type == "class_heritage"), None
        )
        if heritage is not None:
            for clause in heritage.named_children:
                targets = [
                    c
                    for c in clause.named_children
                    if c.type.endswith("identifier")
                    or c.type in ("generic_type", "member_expression")
                ]
                if clause.type == "extends_clause":
                    bases.extend(self.text(t).strip() for t in targets)
                elif clause.type == "implements_clause":
                    implements.extend(self.text(t).strip() for t in targets)
                for t in targets:
                    self.refs.append(
                        ExtractedReference(
                            name=self.text(t).strip(),
                            context="extends" if clause.type == "extends_clause" else "implements",
                            span=rt.span_of(t),
                            scope_path=scope,
                        )
                    )
        body = next(
            (c for c in node.named_children if c.type == "class_body"), None
        )
        mods = _modifiers(node, self.src) + (("export",) if exported else ())
        decs = self._take_decorators(node, scope, name)
        qname = self._emit(
            "class", name, name_node, node, scope,
            signature=self._signature(node, body),
            bases=tuple(bases), implements=tuple(implements),
            modifiers=mods, decorators=decs,
        )
        for b in bases:
            self.relations.append(
                ExtractedRelation("EXTENDS", qname, b, rt.span_of(node))
            )
        for i in implements:
            self.relations.append(
                ExtractedRelation("IMPLEMENTS", qname, i, rt.span_of(node))
            )
        self._walk_children(body, (*scope, name), in_class=True)

    def _handle_interface(self, node: Node, scope: tuple[str, ...],
                          exported: bool) -> None:
        name_node = self._name_node(node)
        if name_node is None:
            return
        name = self.text(name_node)
        ext = next(
            (c for c in node.named_children if c.type == "extends_type_clause"), None
        )
        bases = [
            self.text(c).strip()
            for c in (ext.named_children if ext is not None else [])
            if c.type.endswith("identifier") or c.type == "generic_type"
        ]
        body = next(
            (c for c in node.named_children
             if c.type in ("interface_body", "object_type")),
            None,
        )
        mods = _modifiers(node, self.src) + (("export",) if exported else ())
        qname = self._emit(
            "interface", name, name_node, node, scope,
            signature=self._signature(node, body),
            bases=tuple(bases), modifiers=mods,
            decorators=self._take_decorators(node, scope, name),
        )
        for b in bases:
            self.relations.append(
                ExtractedRelation("EXTENDS", qname, b, rt.span_of(node))
            )
        self._walk_children(body, (*scope, name), in_class=True)

    def _param_names(self, node: Node) -> tuple[str, ...]:
        """Nomes de parâmetros TS (required/optional/rest/destructure)."""
        params = node.child_by_field_name("parameters")
        if params is None:
            params = next(
                (c for c in node.named_children
                 if c.type in ("formal_parameters", "parameters")),
                None,
            )
        if params is None:
            return ()
        names: list[str] = []
        for p in params.named_children:
            pattern = p.child_by_field_name("pattern")
            target = pattern if pattern is not None else p
            names.append(self.text(target).strip())
        return tuple(n for n in names if n)

    def _handle_function(self, node: Node, scope: tuple[str, ...],
                         in_class: bool, exported: bool,
                         name_override: str | None = None,
                         name_node: Node | None = None,
                         fallback_name: str | None = None) -> None:
        nn = name_node or self._name_node(node)
        name = name_override or (
            self.text(nn) if nn is not None else fallback_name
        )
        if name is None:
            return  # anônima sem binding — política: sem identidade
        body = next(
            (c for c in node.named_children
             if c.type in ("statement_block", "class_body")),
            None,
        )
        mods = list(_modifiers(node, self.src))
        if exported:
            mods.append("export")
        if body is None:
            mods.append("signature-only")
        kind = "method" if in_class else "function"
        self._emit(
            kind, name, nn or node, node, scope,
            signature=self._signature(node, body),
            params=self._param_names(node),
            modifiers=tuple(dict.fromkeys(mods)),
            decorators=self._take_decorators(node, scope, name),
        )
        self._walk_children(body, (*scope, name), in_class=False)

    def _handle_declarator(self, node: Node, scope: tuple[str, ...],
                           in_class: bool, exported: bool) -> None:
        name_n = node.child_by_field_name("name")
        value = node.child_by_field_name("value")
        if name_n is None:
            self._walk_children(node, scope, in_class)
            return
        if value is None:
            # `declare const x: T` / `let x` — binding declarado sem
            # inicializador (comum em .d.ts/ambient): símbolo real.
            if name_n.type == "identifier":
                mods = _modifiers(node, self.src) + (
                    ("export",) if exported else ())
                self._emit(
                    "symbol", self.text(name_n), name_n, node, scope,
                    signature=self._signature(node, None)[:200],
                    modifiers=mods,
                )
            return
        if value.type in ("arrow_function", "function_expression", "function"):
            self._handle_function(
                value, scope, in_class, exported,
                name_override=self.text(name_n), name_node=name_n,
            )
            return
        # `const x = require('m')` / `require('m').default` — binding CJS
        # ao módulo (a aresta IMPORTS sai do _handle_call no walk abaixo).
        req_target = self._require_declarator_target(value)
        if req_target is not None:
            self.refs.append(
                ExtractedReference(
                    name=self.text(name_n), context="import-member",
                    span=rt.span_of(name_n), scope_path=scope,
                    target=req_target,
                )
            )
        self._walk(value, scope, in_class)  # valor pode ser jsx_element inteiro

    def _require_declarator_target(self, value: Node) -> str | None:
        """`require('m')` → ``m``; `require('m').prop` → ``m.prop``."""
        call: Node | None = None
        prop: Node | None = None
        if value.type == "call_expression":
            call = value
        elif value.type == "member_expression":
            obj = value.child_by_field_name("object")
            if obj is not None and obj.type == "call_expression":
                call = obj
                prop = value.child_by_field_name("property")
        if call is None:
            return None
        mod = self._require_module_arg(call)
        if mod is None:
            return None
        if prop is not None:
            return f"{mod}.{self.text(prop)}"
        return mod

    def _require_module_arg(self, call: Node) -> str | None:
        """call_expression `require('<literal>')` → path ou None."""
        fn = call.child_by_field_name("function")
        if fn is None or fn.type != "identifier" or self.text(fn) != "require":
            return None
        return self._first_string(call.child_by_field_name("arguments"))

    def _first_string(self, args: Node | None) -> str | None:
        if args is None:
            return None
        s = next(
            (c for c in args.named_children if c.type == "string"), None
        )
        return self.text(s).strip("\"'") if s is not None else None

    def _handle_alias_enum(self, node: Node, scope: tuple[str, ...],
                           exported: bool) -> None:
        name_node = self._name_node(node)
        if name_node is None:
            return
        mods = _modifiers(node, self.src) + (("export",) if exported else ())
        self._emit(
            "symbol", self.text(name_node), name_node, node, scope,
            signature=self._signature(node, None)[:200],
            modifiers=mods,
            decorators=self._take_decorators(
                node, scope, self.text(name_node)),
        )

    # -- imports / exports / calls ------------------------------------

    def _handle_import(self, node: Node, scope: tuple[str, ...]) -> None:
        # `import x = require('m')`: o field `source` mora no
        # `import_require_clause`, **não** no statement (lf-0130 — ler do
        # statement produzia module_path vazio → IMPORTS sem alvo →
        # snapshot FAILED inteiro).
        req = next(
            (c for c in node.named_children
             if c.type == "import_require_clause"), None,
        )
        if req is not None:
            src_n = req.child_by_field_name("source")
            module_path = self._module_path(src_n)
            binding = next(
                (c for c in req.named_children if c.type == "identifier"),
                None,
            )
            # `x` liga ao módulo inteiro (module.exports) — não a membro
            self._emit_module_import(
                node, module_path, scope,
                bindings=[(self.text(binding), module_path)]
                if binding is not None else [],
            )
            return
        src_n = node.child_by_field_name("source")
        module_path = self._module_path(src_n)
        clause = next(
            (c for c in node.named_children if c.type == "import_clause"), None
        )
        names = self._import_names(clause) if clause is not None else []
        bindings: list[tuple[str, str]] = []
        for imported, alias in names:
            local = alias or imported
            member = imported if imported != "*" else "*"
            bindings.append((local, f"{module_path}.{member}"))
        self._emit_module_import(node, module_path, scope, bindings=bindings)

    def _handle_import_alias(self, node: Node, scope: tuple[str, ...]) -> None:
        """`import foo = Bar.Baz` (import_alias) — alias de entidade, não
        de módulo: binding registrado, sem aresta IMPORTS (não há source)."""
        names = node.named_children
        if len(names) < 2:
            return
        binding, target = names[0], names[-1]
        self.refs.append(
            ExtractedReference(
                name=self.text(target), context="import-alias",
                span=rt.span_of(node), scope_path=scope,
            )
        )
        self.refs.append(
            ExtractedReference(
                name=self.text(binding), context="import-member",
                span=rt.span_of(node), scope_path=scope,
                target=self.text(target),
            )
        )

    def _module_path(self, src_n: Node | None) -> str:
        return self.text(src_n).strip("\"'") if src_n is not None else ""

    def _emit_module_import(
        self,
        node: Node,
        module_path: str,
        scope: tuple[str, ...],
        bindings: list[tuple[str, str]],
    ) -> None:
        """IMPORTS + ref de módulo + bindings de membros.

        lf-0130: caminho de módulo não-resolvível NUNCA emite aresta
        IMPORTS vazia — diagnostica e sai; uma aresta sem ``to_text``
        derruba o snapshot inteiro no validate_graph.
        """
        if not module_path:
            self._diag(
                "import-source-missing", "warning",
                "import without resolvable module path — IMPORTS edge "
                "dropped instead of emitted empty",
                node,
            )
            return
        self.refs.append(
            ExtractedReference(
                name=module_path, context="import",
                span=rt.span_of(node), scope_path=scope,
            )
        )
        self.relations.append(
            ExtractedRelation("IMPORTS", self.module, module_path, rt.span_of(node))
        )
        for binding, target in bindings:
            self.refs.append(
                ExtractedReference(
                    name=binding, context="import-member",
                    span=rt.span_of(node), scope_path=scope, target=target,
                )
            )

    def _import_names(self, node: Node) -> list[tuple[str, str | None]]:
        out: list[tuple[str, str | None]] = []
        if node.type == "import_specifier":
            name = node.child_by_field_name("name")
            alias = node.child_by_field_name("alias")
            out.append((self.text(name) if name else self.text(node),
                        self.text(alias) if alias else None))
        elif node.type == "namespace_import":
            alias = next((c for c in node.named_children
                          if c.type == "identifier"), None)
            if alias is not None:
                out.append(("*", self.text(alias)))
        elif node.type == "identifier":
            out.append((self.text(node), None))
        else:
            for c in node.named_children:
                out.extend(self._import_names(c))
        return out

    def _handle_export(self, node: Node, scope: tuple[str, ...],
                       in_class: bool) -> None:
        children = node.named_children
        inner = next(
            (c for c in children
             if c.type in self._DECLS
             or c.type in ("internal_module", "module",
                           "ambient_declaration")),
            None,
        )
        if inner is not None:
            if inner.type in ("internal_module", "module"):
                self._handle_namespace(inner, scope, exported=True)
                return
            default = any(c.type == "default" for c in node.children)
            self._walk_decl(
                inner, scope, in_class, exported=True,
                fallback_name="default" if default else None,
            )
            return
        # `export default <expr>` — function/class anônimas viram
        # `default`; demais expressões viram referência `export`.
        value = node.child_by_field_name("value")
        if value is not None:
            if value.type in ("arrow_function", "function_expression",
                              "function"):
                self._handle_function(
                    value, scope, in_class, True, fallback_name="default",
                )
            elif value.type in ("class", "class_declaration",
                                "abstract_class_declaration"):
                self._handle_class(
                    value, scope, True, fallback_name="default",
                )
            elif self.text(value):
                self.refs.append(
                    ExtractedReference(
                        name=self.text(value), context="export",
                        span=rt.span_of(value), scope_path=scope,
                    )
                )
            return
        src_n = node.child_by_field_name("source")
        module_path = self._module_path(src_n)
        if module_path:
            # export {x} from 'm' / export * from 'm' / export * as ns
            # — dependência real de módulo (re-export): IMPORTS + binding.
            self._emit_module_import(
                node, module_path, scope,
                bindings=self._export_bindings(node, module_path),
            )
            return
        # `export = expr` (CJS/UMD) — a expressão é o module.exports
        # inteiro: referência `export-assignment` (sem aresta própria).
        if any(c.type == "=" for c in node.children) and children:
            self.refs.append(
                ExtractedReference(
                    name=self.text(children[-1]),
                    context="export-assignment",
                    span=rt.span_of(children[-1]), scope_path=scope,
                )
            )
            return
        # `export {a, b}` local — referência ao símbolo exportado.
        for name in self._export_specifier_names(node):
            self.refs.append(
                ExtractedReference(
                    name=name, context="export",
                    span=rt.span_of(node), scope_path=scope,
                )
            )

    def _export_bindings(
        self, node: Node, module_path: str
    ) -> list[tuple[str, str]]:
        """Bindings de re-export: `{a as b} from 'm'` → ``b → m.a``;
        `* as ns from 'm'` → ``ns → m.*``; `* from 'm'` → ``* → m.*``."""
        out: list[tuple[str, str]] = []
        # `*` de `export * from` é filho anônimo — iterar children.
        for c in node.children:
            if c.type == "export_clause":
                for spec in c.named_children:
                    if spec.type != "export_specifier":
                        continue
                    n = spec.child_by_field_name("name")
                    a = spec.child_by_field_name("alias")
                    if n is None:
                        continue
                    out.append(
                        (self.text(a) if a is not None else self.text(n),
                         f"{module_path}.{self.text(n)}")
                    )
            elif c.type == "namespace_export":
                ident = next(
                    (x for x in c.named_children if x.type == "identifier"),
                    None,
                )
                if ident is not None:
                    out.append((self.text(ident), f"{module_path}.*"))
            elif c.type == "*":
                out.append(("*", f"{module_path}.*"))
        return out

    def _export_specifier_names(self, node: Node) -> list[str]:
        out: list[str] = []
        for c in node.named_children:
            if c.type != "export_clause":
                continue
            for spec in c.named_children:
                if spec.type != "export_specifier":
                    continue
                n = spec.child_by_field_name("name")
                if n is not None:
                    out.append(self.text(n))
        return out

    def _handle_namespace(self, node: Node, scope: tuple[str, ...],
                          exported: bool) -> None:
        """`namespace X {}` / `declare module 'x' {}` (internal_module /
        module) — escopo nomeado real: filhos ganham qname `X.*`."""
        name_n = node.child_by_field_name("name")
        if name_n is None:
            name_n = next(
                (c for c in node.named_children
                 if c.type in ("identifier", "nested_identifier", "string")),
                None,
            )
        if name_n is None:
            self._walk_children(node, scope, in_class=False)
            return
        name = self.text(name_n).strip("\"'")
        body = next(
            (c for c in node.named_children
             if c.type == "statement_block"),
            None,
        )
        mods = _modifiers(node, self.src) + (("export",) if exported else ())
        self._emit(
            "symbol", name, name_n, node, scope,
            signature=self._signature(node, body)[:200],
            modifiers=tuple(dict.fromkeys((*mods, "namespace"))),
        )
        self._walk_children(body, (*scope, name), in_class=False)

    def _handle_call(self, node: Node, scope: tuple[str, ...]) -> None:
        callee = node.child_by_field_name("function")
        if callee is None:
            callee = node.child_by_field_name("constructor")
        if callee is None:
            return
        # `import('m')` / `require('m')` — dependência de módulo, não call.
        if callee.type == "import":
            lit = self._first_string(
                node.child_by_field_name("arguments"))
            if lit:
                self._emit_module_import(node, lit, scope, bindings=[])
            else:
                self._diag(
                    "dynamic-import-nonliteral", "info",
                    "import() with non-literal argument — no IMPORTS edge",
                    node,
                )
            return
        if callee.type == "identifier" and self.text(callee) == "require":
            lit = self._first_string(
                node.child_by_field_name("arguments"))
            if lit:
                self._emit_module_import(node, lit, scope, bindings=[])
                return
            # require(expr) não-literal: cai para CALLS (honesto — não
            # comprovável como dependência estática).
        caller = ".".join(scope)
        caller = f"{self.module}.{caller}" if self.module and caller else (caller or self.module)
        self.refs.append(
            ExtractedReference(
                name=self.text(callee), context="call",
                span=rt.span_of(callee), scope_path=scope,
            )
        )
        self.relations.append(
            ExtractedRelation("CALLS", caller, self.text(callee), rt.span_of(callee))
        )

    def _handle_jsx(self, node: Node, scope: tuple[str, ...]) -> None:
        name_n = node.child_by_field_name("name") or next(
            (c for c in node.named_children
             if c.type in ("identifier", "member_expression", "nested_identifier")),
            None,
        )
        if name_n is not None:
            self.refs.append(
                ExtractedReference(
                    name=self.text(name_n), context="jsx",
                    span=rt.span_of(name_n), scope_path=scope,
                )
            )

    # -- walk ----------------------------------------------------------

    _DECLS = (
        "class_declaration", "abstract_class_declaration",
        "interface_declaration", "function_declaration",
        "function_signature", "method_definition", "method_signature",
        "type_alias_declaration", "enum_declaration",
        "lexical_declaration", "variable_declaration",
    )

    def _walk_decl(self, node: Node, scope: tuple[str, ...],
                   in_class: bool, exported: bool = False,
                   fallback_name: str | None = None) -> None:
        t = node.type
        if t in ("class_declaration", "abstract_class_declaration"):
            self._handle_class(node, scope, exported,
                               fallback_name=fallback_name)
        elif t == "interface_declaration":
            self._handle_interface(node, scope, exported)
        elif t in ("function_declaration", "function_signature"):
            self._handle_function(node, scope, in_class, exported,
                                  fallback_name=fallback_name)
        elif t in ("method_definition", "method_signature"):
            self._handle_function(node, scope, True, exported,
                                  fallback_name=fallback_name)
        elif t in ("type_alias_declaration", "enum_declaration"):
            self._handle_alias_enum(node, scope, exported)
        elif t == "ambient_declaration":
            self._walk_ambient(node, scope, in_class, exported,
                               fallback_name)
        elif t in ("lexical_declaration", "variable_declaration"):
            self._walk_vardecl(node, scope, in_class, exported)

    def _walk_ambient(self, node: Node, scope: tuple[str, ...],
                      in_class: bool, exported: bool,
                      fallback_name: str | None) -> None:
        """`declare X` / `export declare X` — unwrap para a decl real
        preservando o flag export (declare global → statement_block)."""
        for c in node.named_children:
            if c.type in ("internal_module", "module"):
                self._handle_namespace(c, scope, exported)
            elif c.type in self._DECLS:
                self._walk_decl(c, scope, in_class, exported,
                                fallback_name)
            else:
                self._walk(c, scope, in_class)

    def _walk_vardecl(self, node: Node, scope: tuple[str, ...],
                      in_class: bool, exported: bool) -> None:
        for c in node.named_children:
            if c.type == "variable_declarator":
                self._handle_declarator(c, scope, in_class, exported)
            else:
                self._walk(c, scope, in_class)

    def _emit_decorator_ref(self, node: Node,
                            scope: tuple[str, ...]) -> None:
        ref = self._decorator_text(node).split("(")[0].strip()
        if ref:
            self.refs.append(
                ExtractedReference(
                    name=ref, context="decorator",
                    span=rt.span_of(node), scope_path=scope,
                )
            )

    def _walk(self, node: Node, scope: tuple[str, ...], in_class: bool) -> None:
        t = node.type
        if t in self._DECLS:
            self._walk_decl(node, scope, in_class)
        elif t == "export_statement":
            self._handle_export(node, scope, in_class)
        elif t == "import_statement":
            self._handle_import(node, scope)
        elif t == "import_alias":
            self._handle_import_alias(node, scope)
        elif t in ("internal_module", "module"):
            self._handle_namespace(node, scope, exported=False)
        elif t == "decorator":
            # decorator anexado dentro de um nó não-declaração (ex.:
            # public_field_definition) — vira ref no escopo atual; os de
            # class_body são capturados por _walk_children antes da decl.
            self._emit_decorator_ref(node, scope)
        else:
            self._walk_leaf(node, scope, in_class)

    def _walk_leaf(self, node: Node, scope: tuple[str, ...],
                   in_class: bool) -> None:
        """Nós não-declaração: calls, JSX e descida genérica."""
        t = node.type
        if t in ("call_expression", "new_expression"):
            self._handle_call(node, scope)
        elif t in ("jsx_opening_element", "jsx_self_closing_element"):
            self._handle_jsx(node, scope)
        for child in node.named_children:
            self._walk(child, scope, in_class)

    def _walk_children(self, node: Node | None, scope: tuple[str, ...],
                       in_class: bool) -> None:
        if node is None:
            return
        for child in node.named_children:
            if child.type == "decorator":
                # decorator de método vem como filho de class_body —
                # fica pendente até a decl seguinte consumi-lo.
                self._pending_decs.append(child)
                continue
            self._walk(child, scope, in_class)
            if self._pending_decs:
                # decorator solto antes de não-declaração (campo etc.):
                # emite a ref no escopo e limpa — nunca vaza para a
                # declaração seguinte errada.
                for d in self._pending_decs:
                    ref = self._decorator_text(d).split("(")[0].strip()
                    if ref:
                        self.refs.append(
                            ExtractedReference(
                                name=ref, context="decorator",
                                span=rt.span_of(d), scope_path=scope,
                            )
                        )
                self._pending_decs = []

    def extract(self, root: Node) -> None:
        self.symbols.append(
            ExtractedSymbol(
                language=self.language,
                kind="module",
                name=self.module,
                qualified_name=self.module,
                span=rt.span_of(root),
            )
        )
        self._walk_children(root, (), in_class=False)
        self.symbols.sort(key=lambda s: (s.span.start_byte, s.qualified_name))


class TypeScriptAdapter:
    """Adaptador TypeScript (.ts/.d.ts) — tree-sitter-typescript."""

    language: str = "typescript"
    extensions: tuple[str, ...] = (".ts", ".d.ts", ".mts", ".cts")
    capabilities: ParserCapability = ParserCapability(
        decorators=True, docstrings=False, interfaces=True
    )
    _spec: rt.LanguageSpec | None = None

    @property
    def adapter_id(self) -> str:
        return "tree-sitter-typescript"

    @property
    def parser_version(self) -> str:
        return rt.parser_version()

    @property
    def grammar_version(self) -> str:
        return self._load().grammar_version

    def _factory(self) -> Any:
        return tree_sitter_typescript.language_typescript

    def _load(self) -> rt.LanguageSpec:
        if self._spec is None:
            self._spec = rt.load_language(self._factory(), "tree-sitter-typescript")
        return self._spec

    def parse(self, request: ParseRequest) -> ParseResult:
        spec = self._load()
        tree = rt.parse_bytes(spec, request.content, request.timeout_us)
        if tree is None:
            return ParseResult(
                path=request.path,
                language=self.language,
                parser=self.adapter_id,
                parser_version=self.parser_version,
                grammar_version=spec.grammar_version,
                content_hash=hashlib.sha256(request.content).hexdigest(),
                has_error=True,
                diagnostics=(
                    ParseDiagnostic(
                        code="parse-timeout",
                        severity="error",
                        message=f"parse exceeded {request.timeout_us}us",
                    ),
                ),
            )
        problems = list(rt.iter_problem_nodes(tree.root_node))
        diagnostics = [
            ParseDiagnostic(
                code="missing-node" if m else "error-node",
                severity="warning" if m else "error",
                message=f"{n.type} at line {n.start_point.row + 1}",
                span=rt.span_of(n),
            )
            for n, m in problems
        ]
        ex = _TsExtractor(request.content, module_name(request.path), self.language)
        ex.extract(tree.root_node)
        return ParseResult(
            path=request.path,
            language=self.language,
            parser=self.adapter_id,
            parser_version=self.parser_version,
            grammar_version=spec.grammar_version,
            content_hash=hashlib.sha256(request.content).hexdigest(),
            has_error=tree.root_node.has_error,
            error_node_count=sum(1 for _, m in problems if not m),
            missing_node_count=sum(1 for _, m in problems if m),
            node_count=rt.node_count(tree.root_node),
            diagnostics=tuple(diagnostics) + tuple(ex.diagnostics),
            symbols=tuple(ex.symbols),
            references=tuple(ex.refs),
            relations=tuple(ex.relations),
        )
