"""Construtor do grafo canônico (Cycle 2 Phase 4).

Puro e determinístico: ``ScanResult`` + ``ParseResult``s →
``GraphBuildResult``. Resolução é apenas de escopo sintático local —
alvos que não resolvem com segurança mantêm o texto e o status
``unresolved``/``ambiguous``. Resolução semântica profunda é Cycle 3.
"""

from __future__ import annotations

from code_atlas.contracts.enums import EdgeKind, NodeKind
from code_atlas.discovery.models import ScanResult
from code_atlas.graph import identity
from code_atlas.parsers.contracts import (
    EvidenceRecord,
    ExtractedReference,
    ExtractedRelation,
    GraphBuildResult,
    GraphEdge,
    GraphNode,
    ParseDiagnostic,
    ParseResult,
    SourceSpan,
)
from code_atlas.resolvers.syntactic.resolver import (
    ModuleCtx,
    ScopeUniverse,
    SyntacticResolver,
)

_RESOLVER_ID = "syntactic-scope-v1"

# alias histórico — o contexto vive em resolvers.syntactic (lf-0016)
_ModuleCtx = ModuleCtx

_SYMBOL_KINDS = {
    "class": NodeKind.CLASS,
    "interface": NodeKind.INTERFACE,
    "function": NodeKind.FUNCTION,
    "method": NodeKind.METHOD,
    "symbol": NodeKind.SYMBOL,
}


class GraphBuilder:
    """Constrói GraphBuildResult a partir de resultados de parsing."""

    @property
    def universe(self) -> ScopeUniverse:
        """Universo de escopo da última ``build()`` — compartilhado com
        resolvers externos (SCIP/LSP) para correlação (lf-0017)."""
        return self._universe

    def build(
        self,
        repo_name: str,
        scan: ScanResult,
        results: list[ParseResult],
    ) -> GraphBuildResult:
        self._nodes: dict[str, GraphNode] = {}
        self._edges: list[GraphEdge] = []
        self._edge_keys: set[tuple[str, str, str | None, str, int]] = set()
        self._evidence: list[EvidenceRecord] = []
        self._evi_index: dict[
            tuple[str, SourceSpan | None, str, str], int
        ] = {}
        self._diagnostics: list[ParseDiagnostic] = []
        # universo de escopo montado durante a construção e compartilhado
        # com o SyntacticResolver na fase de resolução (lf-0016)
        self._universe = ScopeUniverse()
        self._resolver = SyntacticResolver(self._universe)
        self._modules = self._universe.modules  # module_name -> ctx
        self._by_path = self._universe.by_path  # path -> module nid
        self._nid_mod = self._universe.nid_mod  # module nid -> ctx
        self._top_packages = self._universe.top_packages
        self._leaf = self._universe.leaf  # nome simples -> nids (snapshot)
        self._qname_nid = self._universe.qname_nid

        parsed = {r.path: r for r in results if r.supported}
        repo_nid = identity.repo_node_id(repo_name)
        self._nodes[repo_nid] = GraphNode(
            node_id=repo_nid, kind=NodeKind.REPOSITORY, name=repo_name,
            qualified_name=repo_name,
        )

        for f in scan.files:
            self._file_hierarchy(repo_nid, f.path, f.sha256 or "")
        for res in results:
            if res.supported:
                self._module_graph(res)
        for res in results:
            if res.supported:
                self._resolve_relations(res, parsed)

        nodes = tuple(sorted(self._nodes.values(), key=lambda n: n.node_id))
        edges = tuple(
            sorted(
                self._edges,
                key=lambda e: (
                    e.type, e.from_id, e.to_id or "", e.to_text,
                    -1 if e.span is None else e.span.start_byte,
                ),
            )
        )
        stats: dict[str, int] = {"nodes": len(nodes), "edges": len(edges)}
        for n in nodes:
            stats[f"nodes.{n.kind}"] = stats.get(f"nodes.{n.kind}", 0) + 1
        for e in edges:
            stats[f"edges.{e.type}"] = stats.get(f"edges.{e.type}", 0) + 1
        return GraphBuildResult(
            nodes=nodes,
            edges=edges,
            evidence=tuple(self._evidence),
            diagnostics=tuple(self._diagnostics),
            stats=stats,
        )

    # -- nós estruturais ------------------------------------------------

    def _file_hierarchy(self, repo_nid: str, path: str, content_hash: str) -> None:
        parts = path.split("/")
        if len(parts) > 1:
            self._top_packages.add(parts[0])
        parent = repo_nid
        for i in range(1, len(parts)):
            pkg_path = "/".join(parts[:i])
            nid = identity.package_node_id(pkg_path)
            if nid not in self._nodes:
                self._nodes[nid] = GraphNode(
                    node_id=nid, kind=NodeKind.PACKAGE, name=parts[i - 1],
                    qualified_name=pkg_path, path=pkg_path,
                )
            self._edge(EdgeKind.CONTAINS, parent, nid, None, "resolved",
                       "resolved", _RESOLVER_ID, (), None)
            parent = nid
        fid = identity.file_node_id(path)
        self._nodes[fid] = GraphNode(
            node_id=fid, kind=NodeKind.FILE, name=parts[-1],
            qualified_name=path, path=path, content_hash=content_hash,
        )
        self._edge(EdgeKind.CONTAINS, parent, fid, None, "resolved",
                   "resolved", _RESOLVER_ID, (), None)

    def _module_graph(self, res: ParseResult) -> None:
        """Nó de módulo + símbolos + DEFINES hierárquico."""
        mod_syms = [s for s in res.symbols if s.kind == "module"]
        mod_name = mod_syms[0].name if mod_syms else res.path
        mid = identity.module_node_id(res.language, res.path)
        doc = mod_syms[0].docstring if mod_syms else None
        self._nodes[mid] = GraphNode(
            node_id=mid, kind=NodeKind.MODULE, name=mod_name,
            qualified_name=mod_name, language=res.language,
            path=res.path, span=mod_syms[0].span if mod_syms else None,
            content_hash=res.content_hash, docstring=doc,
        )
        ctx = _ModuleCtx(res.path, mod_name, res.language, mid)
        self._modules[mod_name] = ctx
        self._by_path[res.path] = mid
        self._leaf.setdefault(mod_name.split(".")[-1], []).append(mid)
        self._qname_nid.setdefault(mod_name, []).append(mid)

        file_nid = identity.file_node_id(res.path)
        if file_nid in self._nodes:
            self._edge(EdgeKind.CONTAINS, file_nid, mid, None, "resolved",
                       "resolved", _RESOLVER_ID, (), None)

        # símbolos (exceto o pseudo-símbolo module)
        for s in res.symbols:
            if s.kind == "module":
                continue
            kind = _SYMBOL_KINDS.get(s.kind, NodeKind.SYMBOL)
            nid = identity.symbol_node_id(res.language, s.kind, s.qualified_name)
            if nid not in self._nodes:
                self._nodes[nid] = GraphNode(
                    node_id=nid, kind=kind, name=s.name,
                    qualified_name=s.qualified_name, language=res.language,
                    path=res.path, span=s.span, signature=s.signature,
                    params=s.params, docstring=s.docstring,
                    modifiers=s.modifiers,
                )
            ctx.defines.setdefault(s.name, []).append(nid)
            self._leaf.setdefault(s.name, []).append(nid)
            self._qname_nid.setdefault(s.qualified_name, []).append(nid)

            evi = self._evidence_ref(res, s.span)
            parent_name = ".".join(
                (mod_name, *s.scope_path)
            ) if s.scope_path else mod_name
            parent_nids = self._qname_nid.get(parent_name) or [mid]
            self._edge(
                EdgeKind.DEFINES, parent_nids[0], nid, None,
                "resolved", "syntactic", res.parser, evi, s.span,
            )

        # binding local → alvo completo do import (para resolução de nomes)
        for ref in res.references:
            if ref.context == "import-member" and ref.target:
                ctx.import_targets[ref.name] = ref.target
        self._nid_mod[mid] = ctx

    # -- arestas semânticas sintáticas -----------------------------------

    def _resolve_relations(
        self, res: ParseResult, parsed: dict[str, ParseResult]
    ) -> None:
        del parsed  # resolução usa as tabelas já montadas em _module_graph
        ctx = next(
            (c for c in self._modules.values() if c.path == res.path), None
        )
        if ctx is None:
            return
        for rel in res.relations:
            self._edge_from_relation(res, ctx, rel)
        for ref in res.references:
            if ref.context in ("decorator", "type-ref", "jsx"):
                frm = self._scope_nid(ctx, ref.scope_path)
                self._reference_edge(res, ctx, ref, frm)

    def _edge_from_relation(
        self, res: ParseResult, ctx: _ModuleCtx, rel: ExtractedRelation
    ) -> None:
        frm = self._by_qname_or_module(ctx, rel.from_name)
        evi = self._evidence_ref(res, rel.span)
        if frm is None:
            self._diagnostics.append(
                ParseDiagnostic(
                    code="dangling-from",
                    severity="warning",
                    message=f"relation source not found: {rel.from_name}",
                    span=rel.span,
                )
            )
            return
        if rel.type == "IMPORTS":
            to_nid, status = self._resolve_import(ctx, rel.to_name)
            conf = "resolved" if status == "resolved" else "syntactic"
            self._edge(EdgeKind.IMPORTS, frm, to_nid, rel.to_name,
                       status, conf, res.parser, evi, rel.span, res.path)
        elif rel.type in ("EXTENDS", "IMPLEMENTS"):
            to_nid, status = self._resolve_name(ctx, rel.to_name)
            conf = "resolved" if status == "resolved" else "syntactic"
            kind = EdgeKind.EXTENDS if rel.type == "EXTENDS" else EdgeKind.IMPLEMENTS
            self._edge(kind, frm, to_nid, rel.to_name, status, conf,
                       res.parser, evi, rel.span, res.path)
        elif rel.type == "CALLS":
            to_nid, status = self._resolve_call(ctx, rel.to_name)
            conf = "resolved" if status == "resolved" else "syntactic"
            self._edge(EdgeKind.CALLS, frm, to_nid, rel.to_name, status,
                       conf, res.parser, evi, rel.span, res.path)

    def _reference_edge(
        self, res: ParseResult, ctx: _ModuleCtx,
        ref: ExtractedReference, frm: str | None,
    ) -> None:
        if frm is None:
            return
        name, span = ref.name, ref.span
        to_nid, status = self._resolve_name(ctx, name)
        self._edge(
            EdgeKind.REFERENCES, frm, to_nid, name,
            status, "syntactic", res.parser, self._evidence_ref(res, span),
            span, res.path,
        )

    # -- resolução de escopo sintático ----------------------------------
    # Delegada ao SyntacticResolver sobre o ScopeUniverse (lf-0016) —
    # comportamento idêntico ao do Cycle 2, agora atrás do contrato
    # SemanticResolver para composição com SCIP/LSP.

    def _scope_nid(self, ctx: _ModuleCtx, scope_path: tuple[str, ...]) -> str | None:
        return self._resolver.scope_nid(ctx, scope_path)

    def _by_qname_or_module(self, ctx: _ModuleCtx, qname: str) -> str | None:
        return self._resolver.by_qname_or_module(ctx, qname)

    def _resolve_import(
        self, ctx: _ModuleCtx, target: str
    ) -> tuple[str | None, str]:
        """IMPORTS: módulo local → resolved; relativo/path falho →
        unresolved; stdlib/site-packages → external."""
        return self._resolver.resolve_import(ctx, target)

    def _locate(
        self, ctx: _ModuleCtx, target: str
    ) -> tuple[_ModuleCtx | None, list[str]]:
        """Alvo dotted/pathed → (ctx do módulo, segmentos-membro restantes).

        Cobre: python absoluto (`pkg.util`), python relativo (`.util`),
        TS relativo (`./util`, `../x`).
        """
        return self._resolver.locate(ctx, target)

    def _resolve_ts_relative(self, ctx: _ModuleCtx, target: str) -> str | None:
        """TS: `./util` → path relativo ao diretório do arquivo."""
        return self._resolver.resolve_ts_relative(ctx, target)

    def _resolve_name(
        self, ctx: _ModuleCtx, name: str
    ) -> tuple[str | None, str]:
        """Nome bare/dotted → nó local único, senão status defensável.

        Regra: `resolved` só quando há exatamente um alvo no snapshot —
        nunca por coincidência textual.
        """
        return self._resolver.resolve_name(ctx, name)

    def _resolve_call(
        self, ctx: _ModuleCtx, callee: str
    ) -> tuple[str | None, str]:
        return self._resolver.resolve_call(ctx, callee)

    # -- helpers ---------------------------------------------------------

    def _evidence_ref(self, res: ParseResult, span: SourceSpan | None) -> tuple[int, ...]:
        rec = EvidenceRecord(
            path=res.path,
            span=span,
            content_hash=res.content_hash,
            extractor=res.parser,
            extractor_version=f"{res.parser_version}+{res.grammar_version}",
        )
        key = (rec.path, rec.span, rec.content_hash, rec.extractor)
        if key not in self._evi_index:
            self._evi_index[key] = len(self._evidence)
            self._evidence.append(rec)
        return (self._evi_index[key],)

    def _edge(
        self,
        type_: EdgeKind,
        from_id: str,
        to_id: str | None,
        to_text: str | None,
        status: str,
        confidence: str,
        resolver: str,
        evidence: tuple[int, ...],
        span: SourceSpan | None,
        source_path: str = "",
    ) -> None:
        key = (type_, from_id, to_id, to_text or "",
               -1 if span is None else span.start_byte)
        if key in self._edge_keys:
            return
        self._edge_keys.add(key)
        self._edges.append(
            GraphEdge(
                type=type_,
                from_id=from_id,
                to_id=to_id,
                to_text=to_text or "",
                resolution_status=status,
                confidence_class=confidence,
                resolver=resolver,
                evidence_index=evidence,
                span=span,
                source_path=source_path,
            )
        )
