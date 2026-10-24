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
    """`src/a.ts` → ``src.a``; `src/b.d.ts` → ``src.b``."""
    for suffix in (".d.ts", ".tsx", ".ts", ".jsx"):
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

    def text(self, node: Node) -> str:
        return self.src[node.start_byte : node.end_byte].decode("utf-8", "replace")

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

    # -- declarações --------------------------------------------------

    def _handle_class(self, node: Node, scope: tuple[str, ...],
                      exported: bool) -> None:
        name_node = self._name_node(node)
        if name_node is None:
            return
        name = self.text(name_node)
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
        qname = self._emit(
            "class", name, name_node, node, scope,
            signature=self._signature(node, body),
            bases=tuple(bases), implements=tuple(implements),
            modifiers=mods,
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
        )
        for b in bases:
            self.relations.append(
                ExtractedRelation("EXTENDS", qname, b, rt.span_of(node))
            )
        self._walk_children(body, (*scope, name), in_class=True)

    def _handle_function(self, node: Node, scope: tuple[str, ...],
                         in_class: bool, exported: bool,
                         name_override: str | None = None,
                         name_node: Node | None = None) -> None:
        nn = name_node or self._name_node(node)
        if nn is None and name_override is None:
            return  # anônima sem binding — política: sem identidade
        name = name_override or (self.text(nn) if nn else "")
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
            modifiers=tuple(dict.fromkeys(mods)),
        )
        self._walk_children(body, (*scope, name), in_class=False)

    def _handle_declarator(self, node: Node, scope: tuple[str, ...],
                           in_class: bool, exported: bool) -> None:
        name_n = node.child_by_field_name("name")
        value = node.child_by_field_name("value")
        if name_n is None or value is None:
            self._walk_children(node, scope, in_class)
            return
        if value.type in ("arrow_function", "function_expression", "function"):
            self._handle_function(
                value, scope, in_class, exported,
                name_override=self.text(name_n), name_node=name_n,
            )
            return
        self._walk(value, scope, in_class)  # valor pode ser jsx_element inteiro

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
        )

    # -- imports / exports / calls ------------------------------------

    def _handle_import(self, node: Node, scope: tuple[str, ...]) -> None:
        src_n = node.child_by_field_name("source")
        module_path = self.text(src_n).strip("\"'") if src_n else ""
        clause = next(
            (c for c in node.named_children if c.type == "import_clause"), None
        )
        names = self._import_names(clause) if clause is not None else []
        self.refs.append(
            ExtractedReference(
                name=module_path, context="import",
                span=rt.span_of(node), scope_path=scope,
            )
        )
        self.relations.append(
            ExtractedRelation("IMPORTS", self.module, module_path, rt.span_of(node))
        )
        for imported, alias in names:
            self.refs.append(
                ExtractedReference(
                    name=imported, context="import-name",
                    span=rt.span_of(node), scope_path=scope,
                )
            )
            del alias  # alias já preservado no texto do specifier

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
        inner = next(
            (c for c in node.named_children
             if c.type.endswith("declaration") or c.type == "lexical_declaration"),
            None,
        )
        if inner is not None:
            self._walk_decl(inner, scope, in_class, exported=True)
            return
        # export {a, b} / export * from "m" — referência, não declaração
        src_n = node.child_by_field_name("source")
        if src_n is not None:
            module_path = self.text(src_n).strip("\"'")
            self.refs.append(
                ExtractedReference(
                    name=module_path, context="export-from",
                    span=rt.span_of(node), scope_path=scope,
                )
            )

    def _handle_call(self, node: Node, scope: tuple[str, ...]) -> None:
        callee = node.child_by_field_name("function")
        if callee is None:
            callee = node.child_by_field_name("constructor")
        if callee is None:
            return
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
                   in_class: bool, exported: bool = False) -> None:
        t = node.type
        if t in ("class_declaration", "abstract_class_declaration"):
            self._handle_class(node, scope, exported)
        elif t == "interface_declaration":
            self._handle_interface(node, scope, exported)
        elif t in ("function_declaration", "function_signature"):
            self._handle_function(node, scope, in_class, exported)
        elif t in ("method_definition", "method_signature"):
            self._handle_function(node, scope, True, exported)
        elif t in ("type_alias_declaration", "enum_declaration"):
            self._handle_alias_enum(node, scope, exported)
        elif t in ("lexical_declaration", "variable_declaration"):
            for c in node.named_children:
                if c.type == "variable_declarator":
                    self._handle_declarator(c, scope, in_class, exported)
                else:
                    self._walk(c, scope, in_class)

    def _walk(self, node: Node, scope: tuple[str, ...], in_class: bool) -> None:
        t = node.type
        if t in self._DECLS:
            self._walk_decl(node, scope, in_class)
            return
        if t == "export_statement":
            self._handle_export(node, scope, in_class)
            return
        if t == "import_statement":
            self._handle_import(node, scope)
            return
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
            self._walk(child, scope, in_class)

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
            diagnostics=tuple(diagnostics),
            symbols=tuple(ex.symbols),
            references=tuple(ex.refs),
            relations=tuple(ex.relations),
        )
