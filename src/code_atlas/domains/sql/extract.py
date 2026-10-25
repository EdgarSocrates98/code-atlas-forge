"""Extração AST → entidades/relações de domínio SQL (lf-0111, Phase 3).

Semântica das relações emitidas:

- ``transformation`` (1 por statement DML/SELECT com fontes)
  ``READS_FROM`` cada tabela terminal lida (CTEs/subqueries são
  transparentes — a leitura liga à tabela real);
- ``transformation`` ``WRITES_TO`` alvo de INSERT/CTAS/VIEW;
- ``transformation`` ``TRANSFORMS`` alvo de UPDATE/DELETE/MERGE/TRUNCATE
  (mutação in-place, não carga);
- ``target`` ``DERIVES_FROM`` fonte — linhagem tabela→tabela colapsada;
- ``coluna_out`` ``DERIVES_FROM`` ``coluna_src`` — linhagem de coluna
  **somente quando a expressão resolve honestamente** (alias/tabela
  únicos, sem ``SELECT *``, sem subquery de saída desconhecida).

Regras de honestidade (hard):

- ``SELECT *``/``t.*`` em statement produtor → diagnóstico
  ``select-star`` (linhagem de coluna limitada; a de tabela mantém-se);
- colref não-qualificado com ≥2 fontes potenciais → ``ambiguous-column``,
  nunca promovido; qualificador que não casa nenhuma fonte →
  ``unresolved-column``;
- ``${}``/``{{}}``/``EXEC``/fonte dinâmica → ``dynamic-sql`` + relação
  ``to_unresolved`` com o texto preservado — nada fabricado;
- fonte não-tabela (UNNEST, LATERAL VIEW, função-de-tabela) →
  ``unsupported-source`` info — o statement continua extraído;
- qualquer falha interna por statement → ``extract-error``, o resto do
  arquivo continua (parcial honesto, nunca crash).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from code_atlas.domains.contracts import (
    DomainDiagnostic,
    ExtractedDomainEntity,
    ExtractedDomainRelation,
)
from code_atlas.domains.evidence import evidence_record
from code_atlas.domains.identity import DomainIdentity
from code_atlas.domains.model import DomainEntityKind, DomainRelationKind
from code_atlas.domains.sql.nodes import (
    ColRef,
    Cte,
    ExprRefs,
    NameRef,
    SCreateTable,
    SCreateView,
    SDelete,
    SelectStmt,
    SInsert,
    SMerge,
    Source,
    SrcGroup,
    SrcJoin,
    SrcSub,
    SrcTable,
    SrcUnsupported,
    SrcValues,
    SSelect,
    Statement,
    STruncate,
    SUnsupported,
    SUpdate,
    SUse,
)
from code_atlas.domains.sql.parser import ParsedFile
from code_atlas.parsers.contracts import EvidenceRecord, SourceSpan

SQL_DOMAIN = "sql"
_DIAG_CAP = 60        # diagnósticos por artefato — depois vira resumo
_SCOPE_DEPTH = 16     # recursão máxima CTE/subquery — patológico além disso


@dataclass
class _EntAcc:
    identity: DomainIdentity
    kind: DomainEntityKind
    name: str
    span: SourceSpan | None
    attrs: dict[str, str]
    prio: int
    ev: set[int] = field(default_factory=set)


@dataclass
class _RelAcc:
    rtype: str
    from_i: DomainIdentity
    to_i: DomainIdentity | None
    to_text: str
    span: SourceSpan | None
    ev: set[int] = field(default_factory=set)


# --- escopo de resolução de colunas --------------------------------------


@dataclass
class _Out:
    """Coluna de saída nomeada de uma subquery/CTE."""

    name: str | None
    refs: ExprRefs


@dataclass
class _TblSrc:
    ident: DomainIdentity
    parts: tuple[str, ...]
    alias: str | None
    span: SourceSpan


@dataclass
class _SubSrc:
    alias: str | None
    outputs: list[_Out]
    inner: _Scope
    star: bool          # saída parcialmente desconhecida (SELECT *)
    literal: bool = False  # VALUES — nomes conhecidos, sem upstream


@dataclass
class _DeadSrc:
    """Fonte não-resolvível (UNNEST, função, dinâmica) — nunca casa."""

    alias: str | None = None


_SrcInfo = _TblSrc | _SubSrc | _DeadSrc


@dataclass
class _Scope:
    sources: list[_SrcInfo]
    parent: _Scope | None = None


@dataclass
class _RCol:
    terminals: list[tuple[DomainIdentity, str]] = field(default_factory=list)
    problems: list[tuple[str, str]] = field(default_factory=list)


class _FileCtx:
    """Acumulador por artefato: entidades/relações/evidência/diagnósticos."""

    def __init__(
        self, *, repository: str, root: str, environment: str,
        path: str, sha: str, dialect: str, adapter_version: str,
    ) -> None:
        self.repo = repository
        self.root = root
        self.environment = environment
        self.path = path
        self.sha = sha
        self.dialect = dialect
        self.version = adapter_version
        self.entities: dict[str, _EntAcc] = {}
        self.relations: dict[tuple[str, str, str], _RelAcc] = {}
        self.evidence: list[EvidenceRecord] = []
        self._evidx: dict[int, int] = {}
        self.diagnostics: list[DomainDiagnostic] = []
        self.stats: dict[str, int] = {}
        # contexto USE — sequencial dentro do arquivo
        self.use_catalog = ""
        self.use_schema = ""
        self._truncated_diags = 0

    # --- evidência / diagnóstico ---------------------------------------

    def evi(self, stmt_i: int, span: SourceSpan | None) -> int:
        if stmt_i in self._evidx:
            return self._evidx[stmt_i]
        self.evidence.append(
            evidence_record(
                adapter_domain=SQL_DOMAIN,
                adapter_version=self.version,
                path=self.path,
                content_hash=self.sha,
                span=span,
                notes=f"stmt:{stmt_i}",
            )
        )
        idx = len(self.evidence) - 1
        self._evidx[stmt_i] = idx
        return idx

    def diag(self, code: str, severity: str, message: str) -> None:
        if len(self.diagnostics) >= _DIAG_CAP:
            self._truncated_diags += 1
            return
        self.diagnostics.append(
            DomainDiagnostic(
                code=code, severity=severity,
                message=message, path=self.path,
                adapter_id=SQL_DOMAIN,
            )
        )

    def finish_diags(self) -> None:
        if self._truncated_diags:
            self.diagnostics.append(
                DomainDiagnostic(
                    code="diagnostics-truncated", severity="info",
                    message=(
                        f"{self._truncated_diags} further diagnostics "
                        f"suppressed (cap {_DIAG_CAP})"
                    ),
                    path=self.path, adapter_id=SQL_DOMAIN,
                )
            )

    # --- identidades ------------------------------------------------------

    def table_ident(self, parts: tuple[str, ...]) -> DomainIdentity:
        """catalog/namespace/qualified_name a partir das partes do nome."""
        if len(parts) >= 3:
            catalog, namespace, name = (
                parts[0], ".".join(parts[1:-1]), parts[-1]
            )
        elif len(parts) == 2:
            catalog, namespace, name = self.use_catalog, parts[0], parts[1]
        else:
            catalog, namespace, name = (
                self.use_catalog, self.use_schema, parts[0]
            )
        return DomainIdentity(
            domain=SQL_DOMAIN, repository=self.repo, root=self.root,
            catalog=catalog, namespace=namespace, environment=self.environment,
            qualified_name=name,
        )

    def col_ident(self, table: DomainIdentity, col: str) -> DomainIdentity:
        return DomainIdentity(
            domain=SQL_DOMAIN, repository=self.repo, root=self.root,
            catalog=table.catalog, namespace=table.namespace,
            environment=table.environment,
            qualified_name=f"{table.qualified_name}.{col}",
        )

    # --- entidades / relações --------------------------------------------

    def add_entity(
        self,
        ident: DomainIdentity,
        kind: DomainEntityKind,
        name: str,
        span: SourceSpan | None,
        attrs: dict[str, str],
        prio: int,
        evi: int,
    ) -> None:
        key = ident.logical_key()
        acc = self.entities.get(key)
        if acc is None:
            self.entities[key] = _EntAcc(
                identity=ident, kind=kind, name=name, span=span,
                attrs=dict(attrs), prio=prio, ev={evi},
            )
            return
        acc.ev.add(evi)
        if prio > acc.prio:
            acc.kind = kind
            acc.name = name
            acc.span = span
            acc.attrs = dict(attrs)
            acc.prio = prio
        elif prio == acc.prio and acc.kind is not kind:
            self.diag(
                "conflicting-declarations", "warning",
                f"{ident.display()} declared as both "
                f"{acc.kind.value} and {kind.value}",
            )

    def add_rel(
        self,
        rtype: str,
        from_i: DomainIdentity,
        *,
        to_i: DomainIdentity | None = None,
        to_text: str = "",
        span: SourceSpan | None = None,
        evi: int,
    ) -> None:
        to_key = to_i.logical_key() if to_i is not None else f"text:{to_text}"
        key = (rtype, from_i.logical_key(), to_key)
        acc = self.relations.get(key)
        if acc is None:
            self.relations[key] = _RelAcc(
                rtype=rtype, from_i=from_i, to_i=to_i,
                to_text=to_text, span=span, ev={evi},
            )
            return
        acc.ev.add(evi)

    def table_entity(
        self, name: NameRef, evi: int, *, declared: bool, kind: DomainEntityKind
    ) -> DomainIdentity:
        ident = self.table_ident(name.parts)
        attrs = {
            "written_name": name.written,
            "dialect": self.dialect,
            "declared": "create" if declared else "reference",
        }
        self.add_entity(
            ident, kind, name.parts[-1], name.span, attrs,
            prio=3 if declared else 1, evi=evi,
        )
        return ident

    def col_entity(
        self, table: DomainIdentity, col: str, evi: int, *, declared: bool
    ) -> DomainIdentity:
        ident = self.col_ident(table, col)
        self.add_entity(
            ident, DomainEntityKind.DATA_COLUMN, col, None,
            {
                "table": table.display(),
                "dialect": self.dialect,
                "declared": "create" if declared else "reference",
            },
            prio=3 if declared else 1, evi=evi,
        )
        return ident

    def transform_entity(
        self, stmt_i: int, st: Statement, evi: int
    ) -> DomainIdentity:
        qname = f"{self.path}#{stmt_i}"
        ident = DomainIdentity(
            domain=SQL_DOMAIN, repository=self.repo, root=self.root,
            environment=self.environment, qualified_name=qname,
        )
        self.add_entity(
            ident, DomainEntityKind.DATA_TRANSFORMATION,
            f"{st.kind}#{stmt_i}", st.span,
            {
                "artifact": self.path,
                "statement": st.kind,
                "dialect": self.dialect,
            },
            prio=1, evi=evi,
        )
        return ident


# --- coleta de tabelas terminais ------------------------------------------


def _cte_env(ctes: list[Cte], base: dict[str, Cte] | None = None) -> dict[str, Cte]:
    env = dict(base or {})
    for c in ctes:
        env[c.name] = c
    return env


def _srcs_tables(
    srcs: list[Source],
    env: dict[str, Cte],
    out: list[SrcTable],
    dyn: list[SrcUnsupported],
    ctx: _FileCtx,
    depth: int,
    shadow: frozenset[str] = frozenset(),
) -> None:
    for s in srcs:
        if isinstance(s, SrcJoin):
            _srcs_tables([s.left, s.right], env, out, dyn, ctx, depth, shadow)
        elif isinstance(s, SrcGroup):
            _srcs_tables([s.inner], env, out, dyn, ctx, depth, shadow)
        elif isinstance(s, SrcTable):
            _table_read(s, env, out, dyn, ctx, depth, shadow)
        elif isinstance(s, SrcSub):
            if depth < _SCOPE_DEPTH:
                env2 = _cte_env(s.query.ctes, env)
                _query_tables(
                    s.query, env2, out, dyn, ctx, depth + 1, shadow
                )
        elif isinstance(s, SrcUnsupported):
            if s.what == "dynamic":
                dyn.append(s)
            else:
                ctx.diag(
                    "unsupported-source", "info",
                    f"table source not expanded: {s.written} ({s.what})",
                )
        # SrcValues: dados literais — sem upstream


def _table_read(
    s: SrcTable,
    env: dict[str, Cte],
    out: list[SrcTable],
    dyn: list[SrcUnsupported],
    ctx: _FileCtx,
    depth: int,
    shadow: frozenset[str],
) -> None:
    """SrcTable: expande CTE, diagnostica auto-recursão, ou tabela base."""
    bare = len(s.name.parts) == 1
    if bare and s.name.parts[0] in env:
        cte = env[s.name.parts[0]]
        if depth < _SCOPE_DEPTH:
            env2 = _cte_env(cte.query.ctes, env)
            env2.pop(cte.name, None)  # corta auto-recursão
            _query_tables(
                cte.query, env2, out, dyn, ctx, depth + 1,
                shadow | {cte.name},
            )
        return
    if bare and s.name.parts[0] in shadow:
        # auto-referência de CTE recursiva — nunca vira tabela base
        ctx.diag(
            "recursive-cte", "info",
            f"recursive CTE self-reference not expanded: "
            f"{s.name.written}",
        )
        return
    out.append(s)


def _query_tables(
    q: SelectStmt,
    env: dict[str, Cte],
    out: list[SrcTable],
    dyn: list[SrcUnsupported],
    ctx: _FileCtx,
    depth: int,
    shadow: frozenset[str] = frozenset(),
) -> None:
    _srcs_tables(q.sources, env, out, dyn, ctx, depth, shadow)
    for br in q.set_branches:
        env2 = _cte_env(br.ctes, env)
        _query_tables(br, env2, out, dyn, ctx, depth + 1, shadow)
    for sq in q.tail_subqueries:
        env2 = _cte_env(sq.ctes, env)
        _query_tables(sq, env2, out, dyn, ctx, depth + 1, shadow)


# --- escopo de resolução de colunas ---------------------------------------


def _outputs_of(select: SelectStmt) -> tuple[list[_Out], bool]:
    """Saídas nomeadas do select principal; ``star`` = saída desconhecida."""
    outs: list[_Out] = []
    star = False
    for item in select.items:
        if item.star is not None:
            star = True
            continue
        name = item.alias
        if name is None and len(item.refs.cols) == 1 and not item.refs.stars:
            name = item.refs.cols[0].parts[-1]
        outs.append(_Out(name=name, refs=item.refs))
    return outs, star


def _build_scope(
    srcs: list[Source],
    env: dict[str, Cte],
    ctx: _FileCtx,
    parent: _Scope | None,
    depth: int,
    seen: frozenset[int],
) -> _Scope:
    """Constrói o escopo de resolução de uma cláusula FROM."""
    scope = _Scope(sources=[], parent=parent)
    for s in srcs:
        info = _src_info(s, env, ctx, scope, depth, seen)
        if isinstance(info, list):
            scope.sources.extend(info)
        elif info is not None:
            scope.sources.append(info)
    return scope


def _src_info(
    s: Source,
    env: dict[str, Cte],
    ctx: _FileCtx,
    scope: _Scope,
    depth: int,
    seen: frozenset[int],
) -> _SrcInfo | list[_SrcInfo] | None:
    if isinstance(s, SrcJoin):
        left = _src_info(s.left, env, ctx, scope, depth, seen)
        right = _src_info(s.right, env, ctx, scope, depth, seen)
        out: list[_SrcInfo] = []
        for v in (left, right):
            if isinstance(v, list):
                out.extend(v)
            elif v is not None:
                out.append(v)
        return out
    if isinstance(s, SrcGroup):
        inner = _src_info(s.inner, env, ctx, scope, depth, seen)
        inner_list = (
            inner if isinstance(inner, list)
            else [inner] if inner is not None else []
        )
        group_scope = _Scope(sources=inner_list, parent=scope)
        return _SubSrc(
            alias=s.alias, outputs=[], inner=group_scope, star=True,
        )
    if isinstance(s, SrcTable):
        return _src_table_info(s, env, ctx, scope, depth, seen)
    if isinstance(s, SrcSub):
        return _src_sub_info(s, env, ctx, scope, depth, seen)
    if isinstance(s, SrcValues):
        outs = [_Out(name=c, refs=ExprRefs()) for c in s.col_aliases]
        val_scope = _Scope(sources=[], parent=scope)
        return _SubSrc(
            alias=s.alias, outputs=outs, inner=val_scope,
            star=not s.col_aliases, literal=True,
        )
    if isinstance(s, SrcUnsupported):
        return _DeadSrc()
    return None  # type: ignore[unreachable]  # subclasses futuras de Source


def _src_table_info(
    s: SrcTable,
    env: dict[str, Cte],
    ctx: _FileCtx,
    scope: _Scope,
    depth: int,
    seen: frozenset[int],
) -> _SrcInfo:
    """Nome em ``env`` = CTE (vira ``_SubSrc``); senão, tabela real."""
    if len(s.name.parts) == 1 and s.name.parts[0] in env:
        cte = env[s.name.parts[0]]
        if depth >= _SCOPE_DEPTH or id(cte.query) in seen:
            # auto-recursão / profundidade — fonte real, não-resolvível
            return _DeadSrc(alias=s.alias or cte.name)
        env2 = _cte_env(cte.query.ctes, env)
        # o próprio nome fica em env2: auto-referência recai no
        # ``seen`` acima → _DeadSrc, nunca _TblSrc fabricado
        inner = _build_scope(
            cte.query.sources, env2, ctx, scope, depth + 1,
            seen | {id(cte.query)},
        )
        outs, star = _outputs_of(cte.query)
        outputs = _apply_col_aliases(outs, cte.columns)
        return _SubSrc(
            alias=s.alias or cte.name, outputs=outputs,
            inner=inner, star=star,
        )
    return _TblSrc(
        ident=ctx.table_ident(s.name.parts),
        parts=s.name.parts,
        alias=s.alias or s.name.parts[-1],
        span=s.name.span,
    )


def _src_sub_info(
    s: SrcSub,
    env: dict[str, Cte],
    ctx: _FileCtx,
    scope: _Scope,
    depth: int,
    seen: frozenset[int],
) -> _SrcInfo:
    """Subquery parentizada — escopo interno + outputs nomeados."""
    if depth >= _SCOPE_DEPTH or id(s.query) in seen:
        return _DeadSrc(alias=s.alias)
    env2 = _cte_env(s.query.ctes, env)
    inner = _build_scope(
        s.query.sources, env2, ctx, scope, depth + 1,
        seen | {id(s.query)},
    )
    outs, star = _outputs_of(s.query)
    outputs = _apply_col_aliases(outs, s.col_aliases)
    return _SubSrc(
        alias=s.alias, outputs=outputs, inner=inner, star=star,
    )


def _apply_col_aliases(outs: list[_Out], aliases: tuple[str, ...]) -> list[_Out]:
    if not aliases:
        return outs
    out = list(outs)
    for i, a in enumerate(aliases):
        if i < len(out):
            out[i] = _Out(name=a, refs=out[i].refs)
        else:
            out.append(_Out(name=a, refs=ExprRefs()))
    return out


def _may_expose(s: _SrcInfo, name: str) -> bool:
    if isinstance(s, _TblSrc):
        return True
    if isinstance(s, _SubSrc):
        if s.star:
            return True
        return any(o.name == name for o in s.outputs)
    return False


def _match_qual(s: _SrcInfo, qual: tuple[str, ...]) -> bool:
    if isinstance(s, _TblSrc):
        if s.alias is not None:
            return len(qual) == 1 and s.alias == qual[0]
        return len(qual) <= len(s.parts) and s.parts[-len(qual):] == qual
    if isinstance(s, _SubSrc):
        return s.alias is not None and len(qual) == 1 and s.alias == qual[0]
    return False


def _into(
    s: _SrcInfo,
    colname: str,
    ctx: _FileCtx,
    depth: int,
    seen: frozenset[int],
) -> _RCol:
    """Resolve ``s.<colname>`` dentro da fonte — terminais + problemas."""
    res = _RCol()
    if isinstance(s, _TblSrc):
        res.terminals.append((s.ident, colname))
        return res
    if isinstance(s, _SubSrc):
        if s.literal:
            return res  # VALUES — resolvido, sem upstream
        matched = [o for o in s.outputs if o.name == colname]
        if not matched:
            if s.star:
                res.problems.append(
                    ("unresolved-column", f"{colname} in star-expanding "
                     f"source {s.alias!r} — terminal unknown")
                )
            else:
                res.problems.append(
                    ("unresolved-column", f"{colname} not in outputs of "
                     f"{s.alias!r}")
                )
            return res
        for o in matched:
            res = _merge_rcol(
                res, _resolve_refs(o.refs, s.inner, ctx, depth + 1, seen)
            )
        return res
    res.problems.append(
        ("unresolved-column", f"{colname} on opaque source")
    )
    return res


def _merge_rcol(a: _RCol, b: _RCol) -> _RCol:
    a.terminals.extend(b.terminals)
    a.problems.extend(b.problems)
    return a


def _resolve_refs(
    refs: ExprRefs,
    scope: _Scope,
    ctx: _FileCtx,
    depth: int,
    seen: frozenset[int],
) -> _RCol:
    """Terminais de coluna para todos os colrefs de uma expressão.

    Subqueries na expressão expandem com escopo próprio encadeado ao
    pai (correlação honesta).
    """
    res = _RCol()
    if depth >= _SCOPE_DEPTH:
        res.problems.append(("unresolved-column", "resolution depth exceeded"))
        return res
    for col in refs.cols:
        res = _merge_rcol(res, _resolve_col(col, scope, ctx, depth, seen))
    for sq in refs.subqueries:
        if id(sq) in seen:
            continue
        sub_env = _cte_env(sq.ctes, {})
        inner = _build_scope(
            sq.sources, sub_env, ctx, scope, depth + 1, seen | {id(sq)}
        )
        for item in sq.items:
            res = _merge_rcol(
                res,
                _resolve_refs(item.refs, inner, ctx, depth + 1, seen | {id(sq)}),
            )
        for br in sq.set_branches:
            for item in br.items:
                res = _merge_rcol(
                    res,
                    _resolve_refs(
                        item.refs, inner, ctx, depth + 1, seen | {id(sq)}
                    ),
                )
    return res


def _resolve_col(
    col: ColRef,
    scope: _Scope,
    ctx: _FileCtx,
    depth: int,
    seen: frozenset[int],
) -> _RCol:
    res = _RCol()
    parts = col.parts
    if len(parts) == 1:
        name = parts[0]
        cands = [s for s in scope.sources if _may_expose(s, name)]
        if len(cands) == 1:
            return _into(cands[0], name, ctx, depth, seen)
        if len(cands) > 1:
            res.problems.append(
                ("ambiguous-column",
                 f"unqualified {name!r} matches {len(cands)} sources")
            )
            return res
        if scope.parent is not None:
            return _resolve_col(col, scope.parent, ctx, depth + 1, seen)
        res.problems.append(
            ("unresolved-column", f"unqualified {name!r} matches no source")
        )
        return res
    # qualificado: maior prefixo primeiro
    for k in range(len(parts) - 1, 0, -1):
        qual = tuple(parts[:k])
        colname = parts[k]
        matches = [s for s in scope.sources if _match_qual(s, qual)]
        if len(matches) > 1:
            res.problems.append(
                ("ambiguous-column",
                 f"{'.'.join(parts)!r}: qualifier "
                 f"{'.'.join(qual)!r} matches {len(matches)} sources")
            )
            return res
        if len(matches) == 1:
            return _into(matches[0], colname, ctx, depth, seen)
    if scope.parent is not None:
        return _resolve_col(col, scope.parent, ctx, depth + 1, seen)
    res.problems.append(
        ("unresolved-column",
         f"{'.'.join(parts)!r}: qualifier matches no source")
    )
    return res


# --- extração por statement -------------------------------------------------


def _star_diag(ctx: _FileCtx, st: Statement, q: SelectStmt | None) -> None:
    if q is None:
        return
    for item in q.items:
        if item.star is not None:
            qual = item.star.qualifier
            what = f"{'.'.join(qual)}.*" if qual else "*"
            ctx.diag(
                "select-star", "warning",
                f"{st.kind} expands {what} — column lineage is "
                "limited to non-star items; table lineage kept",
            )


def _col_lineage(
    ctx: _FileCtx,
    scope: _Scope,
    pairs: list[tuple[str, ExprRefs]],
    target_i: DomainIdentity,
    evi: int,
    seen: frozenset[int] = frozenset(),
) -> None:
    """Emite DERIVES_FROM col→col para pares (alvo, expr-fonte)."""
    for tcol, refs in pairs:
        res = _resolve_refs(refs, scope, ctx, 0, seen)
        for code, msg in res.problems:
            ctx.diag(code, "warning", msg)
        out_i = ctx.col_entity(target_i, tcol, evi, declared=True)
        for tbl_i, cname in res.terminals:
            src_i = ctx.col_entity(tbl_i, cname, evi, declared=False)
            ctx.add_rel(
                DomainRelationKind.DERIVES_FROM.value, out_i,
                to_i=src_i, evi=evi,
            )


def _extract_file(ctx: _FileCtx, parsed: ParsedFile) -> None:
    for i, st in enumerate(parsed.statements):
        try:
            _stmt(ctx, i, st)
        except Exception as exc:  # nunca crash — parcial honesto
            ctx.diag(
                "extract-error", "error",
                f"{st.kind}#{i}: {type(exc).__name__}: {exc}",
            )
    # problemas do parser viram diagnósticos de domínio
    for d in parsed.diagnostics:
        sev = "error" if d.severity == "error" else "warning"
        ctx.diag(
            d.code if d.code != "error-node" else "parse-error",
            sev, d.message,
        )
    ctx.stats["statements"] = len(parsed.statements)
    ctx.stats["parse_diagnostics"] = len(parsed.diagnostics)


def _stmt(ctx: _FileCtx, i: int, st: Statement) -> None:
    evi = ctx.evi(i, st.span)
    if st.dynamic:
        ctx.diag(
            "dynamic-sql", "warning",
            f"{st.kind}#{i} contains dynamic/template content — "
            "only static parts were extracted",
        )
    if isinstance(st, SUnsupported):
        code = (
            "dynamic-sql" if st.why == "dynamic-exec"
            else "unsupported-statement"
        )
        sev = "warning" if st.why == "dynamic-exec" else "info"
        ctx.diag(code, sev, f"statement skipped: {st.head or st.why}")
        return
    if isinstance(st, SUse):
        _use(ctx, st)
        return
    handler = _STMT_HANDLERS.get(type(st))
    if handler is not None:
        handler(ctx, i, st, evi)


def _use(ctx: _FileCtx, st: SUse) -> None:
    if len(st.parts) >= 2:
        ctx.use_catalog = st.parts[0]
        ctx.use_schema = ".".join(st.parts[1:])
    elif st.parts:
        ctx.use_schema = st.parts[0]


def _read_edges(
    ctx: _FileCtx,
    tid: DomainIdentity,
    sources: list[SrcTable],
    dyn: list[SrcUnsupported],
    evi: int,
) -> None:
    for s in sources:
        ti = ctx.table_entity(s.name, evi, declared=False,
                              kind=DomainEntityKind.DATA_TABLE)
        ctx.add_rel(
            DomainRelationKind.READS_FROM.value, tid,
            to_i=ti, span=s.name.span, evi=evi,
        )
    for d in dyn:
        ctx.add_rel(
            DomainRelationKind.READS_FROM.value, tid,
            to_text=d.written, span=d.span, evi=evi,
        )


def _collect(
    ctx: _FileCtx, env: dict[str, Cte], queries: list[SelectStmt]
) -> tuple[list[SrcTable], list[SrcUnsupported]]:
    tables: list[SrcTable] = []
    dyn: list[SrcUnsupported] = []
    for q in queries:
        env2 = _cte_env(q.ctes, env)
        _query_tables(q, env2, tables, dyn, ctx, 0)
    return tables, dyn


def _derive_edges(
    ctx: _FileCtx,
    target_i: DomainIdentity,
    sources: list[SrcTable],
    evi: int,
) -> None:
    tkey = target_i.logical_key()
    for s in sources:
        ti = ctx.table_entity(s.name, evi, declared=False,
                              kind=DomainEntityKind.DATA_TABLE)
        if ti.logical_key() == tkey:
            continue  # auto-derivação não é aresta
        ctx.add_rel(
            DomainRelationKind.DERIVES_FROM.value, target_i,
            to_i=ti, span=s.name.span, evi=evi,
        )


def _select(ctx: _FileCtx, i: int, st: SSelect, evi: int) -> None:
    q = st.query
    if q is None:
        return
    env = _cte_env(q.ctes)
    tables, dyn = _collect(ctx, env, [q])
    if not tables and not dyn:
        return
    tid = ctx.transform_entity(i, st, evi)
    _read_edges(ctx, tid, tables, dyn, evi)
    _star_diag(ctx, st, q)


def _insert(ctx: _FileCtx, i: int, st: SInsert, evi: int) -> None:
    assert st.target is not None
    tid = ctx.transform_entity(i, st, evi)
    tgt = ctx.table_entity(st.target, evi, declared=False,
                           kind=DomainEntityKind.DATA_TABLE)
    env = _cte_env(st.query.ctes if st.query else [])
    tables, dyn = _collect(ctx, env, [st.query] if st.query else [])
    _read_edges(ctx, tid, tables, dyn, evi)
    ctx.add_rel(
        DomainRelationKind.WRITES_TO.value, tid,
        to_i=tgt, span=st.target.span, evi=evi,
    )
    _derive_edges(ctx, tgt, tables, evi)
    if st.query is not None:
        _star_diag(ctx, st, st.query)
        if st.columns is None:
            ctx.diag(
                "implicit-column-list", "info",
                "INSERT without column list — target column names "
                "unknown; column lineage skipped",
            )
        else:
            pairs = _positional_pairs(st.columns, st.query)
            if pairs is None:
                ctx.diag(
                    "column-count-mismatch", "warning",
                    f"INSERT column list ({len(st.columns)}) does not "
                    f"match select items — mapped common prefix",
                )
                pairs = _positional_pairs(st.columns, st.query, strict=False)
            scope = _build_scope(
                st.query.sources, env, ctx, None, 0, frozenset()
            )
            _col_lineage(ctx, scope, pairs or [], tgt, evi)
    elif st.value_rows:
        # VALUES literal — sem upstream; subqueries nas linhas são lidas
        sub_tables: list[SrcTable] = []
        sub_dyn: list[SrcUnsupported] = []
        for row in st.value_rows:
            for refs in row:
                for sq in refs.subqueries:
                    _query_tables(sq, dict(env), sub_tables, sub_dyn, ctx, 0)
        _read_edges(ctx, tid, sub_tables, sub_dyn, evi)
        if st.columns is None:
            ctx.diag(
                "implicit-column-list", "info",
                "INSERT … VALUES without column list — "
                "column lineage skipped",
            )


def _positional_pairs(
    cols: tuple[str, ...], q: SelectStmt, *, strict: bool = True
) -> list[tuple[str, ExprRefs]] | None:
    items = q.items
    if strict and len(cols) != len(items):
        return None
    return [
        (c, items[k].refs)
        for k, c in enumerate(cols)
        if k < len(items)
    ]


def _update(ctx: _FileCtx, i: int, st: SUpdate, evi: int) -> None:
    assert st.target is not None
    tid = ctx.transform_entity(i, st, evi)
    tgt = ctx.table_entity(st.target, evi, declared=False,
                           kind=DomainEntityKind.DATA_TABLE)
    env = _cte_env(st.ctes)
    tables: list[SrcTable] = []
    dyn: list[SrcUnsupported] = []
    _srcs_tables(st.sources, env, tables, dyn, ctx, 0)
    for q in st.tail_subqueries:
        _query_tables(q, dict(env), tables, dyn, ctx, 0)
    for _, refs in st.sets:
        for sq in refs.subqueries:
            _query_tables(sq, dict(env), tables, dyn, ctx, 0)
    ctx.add_rel(
        DomainRelationKind.READS_FROM.value, tid,
        to_i=tgt, span=st.target.span, evi=evi,
    )
    _read_edges(ctx, tid, tables, dyn, evi)
    ctx.add_rel(
        DomainRelationKind.TRANSFORMS.value, tid,
        to_i=tgt, span=st.target.span, evi=evi,
    )
    _derive_edges(ctx, tgt, tables, evi)
    # escopo: o próprio alvo + fontes da cláusula FROM
    tsrc = _TblSrc(
        ident=tgt, parts=st.target.parts,
        alias=st.target_alias or st.target.parts[-1],
        span=st.target.span,
    )
    scope = _Scope(sources=[tsrc])
    for s in st.sources:
        info = _src_info(s, env, ctx, scope, 0, frozenset())
        if isinstance(info, list):
            scope.sources.extend(info)
        elif info is not None:
            scope.sources.append(info)
    _col_lineage(ctx, scope, list(st.sets), tgt, evi)


def _delete(ctx: _FileCtx, i: int, st: SDelete, evi: int) -> None:
    assert st.target is not None
    tid = ctx.transform_entity(i, st, evi)
    tgt = ctx.table_entity(st.target, evi, declared=False,
                           kind=DomainEntityKind.DATA_TABLE)
    env = _cte_env(st.ctes)
    tables: list[SrcTable] = []
    dyn: list[SrcUnsupported] = []
    _srcs_tables(st.sources, env, tables, dyn, ctx, 0)
    for q in st.tail_subqueries:
        _query_tables(q, dict(env), tables, dyn, ctx, 0)
    ctx.add_rel(
        DomainRelationKind.READS_FROM.value, tid,
        to_i=tgt, span=st.target.span, evi=evi,
    )
    _read_edges(ctx, tid, tables, dyn, evi)
    ctx.add_rel(
        DomainRelationKind.TRANSFORMS.value, tid,
        to_i=tgt, span=st.target.span, evi=evi,
    )
    _derive_edges(ctx, tgt, tables, evi)


def _merge(ctx: _FileCtx, i: int, st: SMerge, evi: int) -> None:
    assert st.target is not None and st.source is not None
    tid = ctx.transform_entity(i, st, evi)
    tgt = ctx.table_entity(st.target, evi, declared=False,
                           kind=DomainEntityKind.DATA_TABLE)
    env = _cte_env(st.ctes)
    tables: list[SrcTable] = []
    dyn: list[SrcUnsupported] = []
    _srcs_tables([st.source], env, tables, dyn, ctx, 0)
    for q in st.tail_subqueries:
        _query_tables(q, dict(env), tables, dyn, ctx, 0)
    for _, refs in st.sets:
        for sq in refs.subqueries:
            _query_tables(sq, dict(env), tables, dyn, ctx, 0)
    for refs in st.insert_exprs:
        for sq in refs.subqueries:
            _query_tables(sq, dict(env), tables, dyn, ctx, 0)
    ctx.add_rel(
        DomainRelationKind.READS_FROM.value, tid,
        to_i=tgt, span=st.target.span, evi=evi,
    )
    _read_edges(ctx, tid, tables, dyn, evi)
    ctx.add_rel(
        DomainRelationKind.TRANSFORMS.value, tid,
        to_i=tgt, span=st.target.span, evi=evi,
    )
    _derive_edges(ctx, tgt, tables, evi)
    # escopo: alvo + fonte do USING
    scope = _Scope(sources=[])
    scope.sources.append(
        _TblSrc(
            ident=tgt, parts=st.target.parts,
            alias=st.target_alias or st.target.parts[-1],
            span=st.target.span,
        )
    )
    info = _src_info(st.source, env, ctx, scope, 0, frozenset())
    if isinstance(info, list):
        scope.sources.extend(info)
    elif info is not None:
        scope.sources.append(info)
    _col_lineage(ctx, scope, list(st.sets), tgt, evi)
    if st.insert_cols is not None and st.insert_exprs:
        pairs = [
            (c, st.insert_exprs[k])
            for k, c in enumerate(st.insert_cols)
            if k < len(st.insert_exprs)
        ]
        if len(st.insert_cols) != len(st.insert_exprs):
            ctx.diag(
                "column-count-mismatch", "warning",
                "MERGE INSERT column list does not match VALUES arity",
            )
        _col_lineage(ctx, scope, pairs, tgt, evi)


def _create_table(ctx: _FileCtx, i: int, st: SCreateTable, evi: int) -> None:
    assert st.name is not None
    tgt = ctx.table_entity(st.name, evi, declared=True,
                           kind=DomainEntityKind.DATA_TABLE)
    for cname, ctype in st.columns:
        ident = ctx.col_ident(tgt, cname)
        ctx.add_entity(
            ident, DomainEntityKind.DATA_COLUMN, cname, None,
            {
                "table": tgt.display(),
                "declared_type": ctype,
                "dialect": ctx.dialect,
                "declared": "create",
            },
            prio=3, evi=evi,
        )
    if st.like is not None:
        tid = ctx.transform_entity(i, st, evi)
        li = ctx.table_entity(st.like, evi, declared=False,
                              kind=DomainEntityKind.DATA_TABLE)
        ctx.add_rel(
            DomainRelationKind.READS_FROM.value, tid,
            to_i=li, span=st.like.span, evi=evi,
        )
        ctx.add_rel(
            DomainRelationKind.WRITES_TO.value, tid,
            to_i=tgt, span=st.name.span, evi=evi,
        )
        ctx.add_rel(
            DomainRelationKind.DERIVES_FROM.value, tgt,
            to_i=li, span=st.like.span, evi=evi,
        )
    if st.query is not None:
        tid = ctx.transform_entity(i, st, evi)
        env = _cte_env(st.query.ctes)
        tables, dyn = _collect(ctx, env, [st.query])
        _read_edges(ctx, tid, tables, dyn, evi)
        ctx.add_rel(
            DomainRelationKind.WRITES_TO.value, tid,
            to_i=tgt, span=st.name.span, evi=evi,
        )
        _derive_edges(ctx, tgt, tables, evi)
        _star_diag(ctx, st, st.query)
        pairs = _named_pairs(ctx, st.query)
        scope = _build_scope(
            st.query.sources, env, ctx, None, 0, frozenset()
        )
        _col_lineage(ctx, scope, pairs, tgt, evi)


def _create_view(ctx: _FileCtx, i: int, st: SCreateView, evi: int) -> None:
    assert st.name is not None and st.query is not None
    tgt = ctx.table_entity(st.name, evi, declared=True,
                           kind=DomainEntityKind.DATA_VIEW)
    tid = ctx.transform_entity(i, st, evi)
    env = _cte_env(st.query.ctes)
    tables, dyn = _collect(ctx, env, [st.query])
    _read_edges(ctx, tid, tables, dyn, evi)
    ctx.add_rel(
        DomainRelationKind.WRITES_TO.value, tid,
        to_i=tgt, span=st.name.span, evi=evi,
    )
    _derive_edges(ctx, tgt, tables, evi)
    _star_diag(ctx, st, st.query)
    pairs = _named_pairs(ctx, st.query, col_aliases=st.col_aliases)
    scope = _build_scope(
        st.query.sources, env, ctx, None, 0, frozenset()
    )
    _col_lineage(ctx, scope, pairs, tgt, evi)


def _named_pairs(
    ctx: _FileCtx,
    q: SelectStmt,
    col_aliases: tuple[str, ...] = (),
) -> list[tuple[str, ExprRefs]]:
    """Pares (coluna-saída, refs) por nome — VIEW/CTAS.

    Nome vem de col_aliases posicionais, alias do item ou colref simples.
    Itens sem nome → diagnóstico ``unnamed-output`` e skip.
    """
    pairs: list[tuple[str, ExprRefs]] = []
    for k, item in enumerate(q.items):
        name: str | None = None
        if k < len(col_aliases):
            name = col_aliases[k]
        if name is None:
            name = item.alias
        if name is None and len(item.refs.cols) == 1 and not item.refs.stars:
            name = item.refs.cols[0].parts[-1]
        if name is None:
            if item.star is None:
                ctx.diag(
                    "unnamed-output", "info",
                    "output column has no resolvable name — "
                    "column lineage skipped for it",
                )
            continue
        pairs.append((name, item.refs))
    return pairs


def _truncate(ctx: _FileCtx, i: int, st: STruncate, evi: int) -> None:
    assert st.name is not None
    tid = ctx.transform_entity(i, st, evi)
    tgt = ctx.table_entity(st.name, evi, declared=False,
                           kind=DomainEntityKind.DATA_TABLE)
    ctx.add_rel(
        DomainRelationKind.TRANSFORMS.value, tid,
        to_i=tgt, span=st.name.span, evi=evi,
    )


# Dispatch por tipo de statement — ``_use``/``SUnsupported`` ficam fora
# porque têm assinatura e semântica próprias (sem evidência de statement).
_STMT_HANDLERS: dict[
    type[Statement], Callable[[_FileCtx, int, Any, int], None]
] = {
    SSelect: _select,
    SInsert: _insert,
    SUpdate: _update,
    SDelete: _delete,
    SMerge: _merge,
    SCreateTable: _create_table,
    SCreateView: _create_view,
    STruncate: _truncate,
}


# --- ponto de entrada --------------------------------------------------------


@dataclass
class FileExtraction:
    entities: list[ExtractedDomainEntity]
    relations: list[ExtractedDomainRelation]
    evidence: list[EvidenceRecord]
    diagnostics: list[DomainDiagnostic]
    stats: dict[str, int]


def extract_parsed(
    parsed: ParsedFile,
    *,
    repository: str,
    root: str,
    environment: str,
    path: str,
    sha: str,
    adapter_version: str,
) -> FileExtraction:
    """Converte ``ParsedFile`` em fatos de domínio de um artefato."""
    ctx = _FileCtx(
        repository=repository, root=root, environment=environment,
        path=path, sha=sha, dialect=parsed.dialect,
        adapter_version=adapter_version,
    )
    _extract_file(ctx, parsed)
    ctx.finish_diags()
    entities = [
        ExtractedDomainEntity(
            identity=acc.identity, kind=acc.kind, name=acc.name,
            path=path, span=acc.span, content_hash=sha,
            attributes=acc.attrs, evidence=tuple(sorted(acc.ev)),
        )
        for acc in ctx.entities.values()
    ]
    relations = [
        ExtractedDomainRelation(
            relation_type=acc.rtype, path=path,
            from_identity=acc.from_i, to_identity=acc.to_i,
            to_unresolved=acc.to_text, span=acc.span,
            evidence=tuple(sorted(acc.ev)),
        )
        for acc in ctx.relations.values()
    ]
    stats = dict(ctx.stats)
    stats["entities"] = len(entities)
    stats["relations"] = len(relations)
    stats["diagnostics"] = len(ctx.diagnostics)
    return FileExtraction(
        entities=entities, relations=relations,
        evidence=ctx.evidence, diagnostics=ctx.diagnostics, stats=stats,
    )
