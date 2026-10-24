"""Adaptador Python via tree-sitter-python — extração estrutural real.

Node types cobertos: module, class_definition, function_definition
(async incluído), decorated_definition, import_statement,
import_from_statement, call. Escopo léxico via pilha de nomes — funções
aninhadas e métodos recebem qualified names distintos.
"""

from __future__ import annotations

import hashlib

import tree_sitter_python
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

_DEFS = ("function_definition", "class_definition")


def module_name(path: str) -> str:
    """`pkg/mod.py` → ``pkg.mod``; `pkg/__init__.py` → ``pkg``."""
    parts = path[:-3].split("/")  # remove .py
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(p for p in parts if p) or path


class _PyExtractor:
    """Visitor sobre a árvore; produz símbolos, referências e relações."""

    def __init__(self, src: bytes, module: str) -> None:
        self.src = src
        self.module = module
        self.symbols: list[ExtractedSymbol] = []
        self.refs: list[ExtractedReference] = []
        self.relations: list[ExtractedRelation] = []

    def text(self, node: Node) -> str:
        return self.src[node.start_byte : node.end_byte].decode("utf-8", "replace")

    # -- símbolos -----------------------------------------------------

    def _emit(self, kind: str, name: str, name_node: Node, node: Node,
              scope: tuple[str, ...], **kw: object) -> str:
        qname = ".".join((*scope, name)) if scope else name
        qname = f"{self.module}.{qname}" if self.module else qname
        self.symbols.append(
            ExtractedSymbol(
                language="python",
                kind=kind,
                name=name,
                qualified_name=qname,
                span=rt.span_of(node),
                name_span=rt.span_of(name_node),
                scope_path=scope,
                **kw,  # type: ignore[arg-type]
            )
        )
        return qname

    def _signature(self, node: Node, body: Node | None) -> str:
        end = body.start_byte if body is not None else node.end_byte
        return self.src[node.start_byte : end].decode("utf-8", "replace").rstrip(" :\n")

    def _docstring(self, body: Node | None) -> str | None:
        if body is None or body.named_child_count == 0:
            return None
        first = body.named_children[0]
        if first.type != "expression_statement" or first.named_child_count == 0:
            return None
        s = first.named_children[0]
        if s.type not in ("string", "concatenated_string"):
            return None
        return self.text(s).strip("\"' \n")[:500]

    def _handle_class(self, node: Node, scope: tuple[str, ...],
                      decorators: tuple[str, ...]) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.text(name_node)
        bases: list[str] = []
        sup = node.child_by_field_name("superclasses")
        if sup is not None:
            for c in sup.named_children:
                bases.append(self.text(c).strip())
                self.refs.append(
                    ExtractedReference(
                        name=self.text(c).strip(),
                        context="extends",
                        span=rt.span_of(c),
                        scope_path=scope,
                    )
                )
        body = node.child_by_field_name("body")
        qname = self._emit(
            "class", name, name_node, node, scope,
            signature=self._signature(node, body),
            docstring=self._docstring(body),
            decorators=decorators,
            bases=tuple(bases),
        )
        for base in bases:
            self.relations.append(
                ExtractedRelation(
                    type="EXTENDS",
                    from_name=qname,
                    to_name=base.split("(")[0].strip(),
                    span=rt.span_of(node),
                )
            )
        self._walk_children(body, (*scope, name), in_class=True)

    def _param_names(self, parameters: Node | None) -> tuple[str, ...]:
        """Nomes de parâmetros (typed/default/splat inclusos)."""
        if parameters is None:
            return ()
        names: list[str] = []
        for p in parameters.named_children:
            name_node = p.child_by_field_name("name")
            if name_node is not None:
                names.append(self.text(name_node))
            elif p.type == "identifier":
                names.append(self.text(p))
            elif p.type in ("list_splat_pattern", "dictionary_splat_pattern"):
                inner = next(
                    (c for c in p.named_children if c.type == "identifier"),
                    None,
                )
                if inner is not None:
                    names.append(self.text(inner))
        return tuple(names)

    def _handle_function(self, node: Node, scope: tuple[str, ...],
                         decorators: tuple[str, ...], in_class: bool) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.text(name_node)
        mods = list(decorators_to_mods(decorators))
        if any(c.type == "async" for c in node.children):
            mods.append("async")
        body = node.child_by_field_name("body")
        params = node.child_by_field_name("parameters")
        kind = "method" if in_class else "function"
        self._emit(
            kind, name, name_node, node, scope,
            signature=self._signature(node, body),
            params=self._param_names(params),
            docstring=self._docstring(body),
            decorators=decorators,
            modifiers=tuple(mods),
        )
        self._walk_children(body, (*scope, name), in_class=False)

    # -- imports / calls ----------------------------------------------

    def _handle_import(self, node: Node, scope: tuple[str, ...]) -> None:
        if node.type == "import_statement":
            for c in node.named_children:
                target, alias = self._import_pair(c)
                # `import a.b` liga o nome 'a'; `import a.b as c` liga 'c'
                binding = alias or target.split(".")[0]
                self._emit_import(self.module, target, binding, target, c, scope)
        else:  # import_from_statement
            mod = node.child_by_field_name("module_name")
            base = self.text(mod) if mod is not None else ""
            for c in node.named_children:
                # `is not` NÃO funciona entre wrappers Node — comparar .id
                if c.type in ("dotted_name", "aliased_import") and (
                    mod is None or c.id != mod.id
                ):
                    target, alias = self._import_pair(c)
                    full = f"{base}.{target}" if base else target
                    binding = alias or target.split(".")[-1]
                    self._emit_import(self.module, full, binding, full, c, scope)

    def _import_pair(self, node: Node) -> tuple[str, str | None]:
        if node.type == "aliased_import":
            name = node.child_by_field_name("name")
            alias = node.child_by_field_name("alias")
            return (
                self.text(name) if name else self.text(node),
                self.text(alias) if alias else None,
            )
        return self.text(node), None

    def _emit_import(self, src_qname: str, target: str, binding: str,
                     member_target: str, node: Node, scope: tuple[str, ...]) -> None:
        self.refs.append(
            ExtractedReference(
                name=target, context="import",
                span=rt.span_of(node), scope_path=scope,
            )
        )
        # binding local → alvo completo: alimenta a resolução do grafo
        self.refs.append(
            ExtractedReference(
                name=binding, context="import-member",
                span=rt.span_of(node), scope_path=scope, target=member_target,
            )
        )
        self.relations.append(
            ExtractedRelation(
                type="IMPORTS", from_name=src_qname, to_name=target,
                span=rt.span_of(node),
            )
        )

    def _handle_call(self, node: Node, scope: tuple[str, ...]) -> None:
        fn = node.child_by_field_name("function")
        if fn is None:
            return
        self.refs.append(
            ExtractedReference(
                name=self.text(fn), context="call",
                span=rt.span_of(fn), scope_path=scope,
            )
        )
        caller = ".".join(scope)
        caller = f"{self.module}.{caller}" if self.module and caller else caller or self.module
        self.relations.append(
            ExtractedRelation(
                type="CALLS", from_name=caller, to_name=self.text(fn),
                span=rt.span_of(fn),
            )
        )

    # -- walk ----------------------------------------------------------

    def _walk_children(self, node: Node | None, scope: tuple[str, ...],
                       in_class: bool) -> None:
        if node is None:
            return
        for child in node.named_children:
            self._walk(child, scope, in_class)

    def _walk(self, node: Node, scope: tuple[str, ...], in_class: bool) -> None:
        t = node.type
        if t == "decorated_definition":
            decs = tuple(
                self.text(c).lstrip("@").strip()
                for c in node.children
                if c.type == "decorator"
            )
            inner = next((c for c in node.children if c.type in _DEFS), None)
            if inner is not None:
                if inner.type == "class_definition":
                    self._handle_class(inner, scope, decs)
                else:
                    self._handle_function(inner, scope, decs, in_class)
            return
        if t == "class_definition":
            self._handle_class(node, scope, ())
            return
        if t == "function_definition":
            self._handle_function(node, scope, (), in_class)
            return
        if t in ("import_statement", "import_from_statement"):
            self._handle_import(node, scope)
            return
        if t == "call":
            self._handle_call(node, scope)
            # continua descendo — chamadas aninhadas nos argumentos
        for child in node.named_children:
            self._walk(child, scope, in_class)

    def extract(self, root: Node) -> None:
        module_span = rt.span_of(root)
        self.symbols.append(
            ExtractedSymbol(
                language="python",
                kind="module",
                name=self.module,
                qualified_name=self.module,
                span=module_span,
                docstring=self._docstring(
                    root if root.type == "module" else None
                ) or self._module_doc(root),
            )
        )
        self._walk_children(root, (), in_class=False)
        self.symbols.sort(key=lambda s: (s.span.start_byte, s.qualified_name))

    def _module_doc(self, root: Node) -> str | None:
        return self._docstring(root)


def decorators_to_mods(decorators: tuple[str, ...]) -> tuple[str, ...]:
    """Decorators conhecidos viram modifiers (staticmethod etc.)."""
    known = {"staticmethod", "classmethod", "abstractmethod", "property", "overload"}
    return tuple(d for d in decorators if d.split("(")[0].split(".")[-1] in known)


class PythonAdapter:
    """Adaptador Python — tree-sitter-python pinned."""

    language: str = "python"
    extensions: tuple[str, ...] = (".py", ".pyi")
    capabilities: ParserCapability = ParserCapability(decorators=True)
    _spec: rt.LanguageSpec | None = None

    @property
    def adapter_id(self) -> str:
        return "tree-sitter-python"

    @property
    def parser_version(self) -> str:
        return rt.parser_version()

    @property
    def grammar_version(self) -> str:
        return self._load().grammar_version

    def _load(self) -> rt.LanguageSpec:
        if self._spec is None:
            self._spec = rt.load_language(
                tree_sitter_python.language, "tree-sitter-python"
            )
        return self._spec

    def parse(self, request: ParseRequest) -> ParseResult:
        spec = self._load()
        tree = rt.parse_bytes(spec, request.content, request.timeout_us)
        if tree is None:
            return ParseResult(
                path=request.path,
                language="python",
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
        diagnostics = [
            ParseDiagnostic(
                code="missing-node" if is_missing else "error-node",
                severity="warning" if is_missing else "error",
                message=f"{n.type} at line {n.start_point.row + 1}",
                span=rt.span_of(n),
            )
            for n, is_missing in rt.iter_problem_nodes(tree.root_node)
        ]
        ex = _PyExtractor(request.content, module_name(request.path))
        ex.extract(tree.root_node)
        err = sum(1 for n, m in rt.iter_problem_nodes(tree.root_node) if not m)
        miss = sum(1 for _, m in rt.iter_problem_nodes(tree.root_node) if m)
        return ParseResult(
            path=request.path,
            language="python",
            parser=self.adapter_id,
            parser_version=self.parser_version,
            grammar_version=spec.grammar_version,
            content_hash=hashlib.sha256(request.content).hexdigest(),
            has_error=tree.root_node.has_error,
            error_node_count=err,
            missing_node_count=miss,
            node_count=rt.node_count(tree.root_node),
            diagnostics=tuple(diagnostics),
            symbols=tuple(ex.symbols),
            references=tuple(ex.refs),
            relations=tuple(ex.relations),
        )
