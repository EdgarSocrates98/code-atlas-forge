"""Delegação ``spark.sql(literal)`` → parser do adapter ``sql`` (lf-0112).

Costura pública do adapter SQL (lf-0111): ``parse_file`` (stdlib puro)
produz o ``ParsedFile``; ``extract_parsed`` produz o ``FileExtraction``
com entidades/relações/diagnósticos de linhagem ANSI/Spark já
computados — CTEs colapsados, ``SELECT *`` declarado, dinâmico
diagnosticado.

O remap para o domínio ``pyspark`` é mecânico e honesto:

- entidades de tabela/view/coluna são re-emitidas com
  ``domain="pyspark"`` (o contrato exige ``identity.domain`` ==
  adapter) — mesma posição catalog/namespace/name;
- as transformações internas do SQL (``<path>#<i>``) **colapsam** na
  transformação do call-site — ``T_call`` herdada do walker;
- relações ``READS_FROM``/``WRITES_TO``/``TRANSFORMS``/
  ``DERIVES_FROM`` tabela→tabela e coluna→coluna são re-ancoradas;
- evidência é **sempre** o span do ``spark.sql(…)`` no ``.py`` — spans
  relativos ao literal descartados (não são endereçáveis no arquivo);
- diagnósticos do parser/extrator fluem prefixados
  ``spark.sql@L<linha>:`` — ``dynamic-sql``, ``select-star``,
  ``parse-error`` e demais chegam honestos ao envelope.

Retorna ``(sources, unresolved)``: chaves lógicas das fontes lidas
(entram no colapso ``sink DERIVES_FROM fonte`` dos writes) e flag de
linhagem parcial do literal.
"""

from __future__ import annotations

import hashlib

# Tipo do literal observado pelo walker — importado apenas para tipagem
# (walk → sqllit → walk seria circular em runtime; TYPE_CHECKING cobre
# os dois lados).
from typing import TYPE_CHECKING

from code_atlas.domains.identity import DomainIdentity
from code_atlas.domains.model import DomainEntityKind, DomainRelationKind
from code_atlas.domains.spark.emit import Sink
from code_atlas.domains.sql import parse_file
from code_atlas.domains.sql.adapter import ADAPTER_VERSION as _SQL_VERSION
from code_atlas.domains.sql.extract import FileExtraction, extract_parsed
from code_atlas.parsers.contracts import SourceSpan

if TYPE_CHECKING:
    from code_atlas.domains.spark.walk import _SqlLit

_WRITE_REL = frozenset(
    {
        DomainRelationKind.WRITES_TO.value,
        DomainRelationKind.TRANSFORMS.value,
    }
)


def delegate_sql_literal(
    sink: Sink,
    lit: _SqlLit,
    path: str,
    sha: str,
    *,
    call_evi: int,
    call_span: SourceSpan | None,
    t_ident: DomainIdentity,
) -> tuple[set[str], bool]:
    """Extrai deps de tabela de ``spark.sql(literal)`` re-ancoradas em ``t_ident``.

    Devolve ``(logical_keys das fontes, houve_alvo_unresolved)``.
    """
    req = sink.request
    sql_sha = hashlib.sha256(lit.literal.encode("utf-8")).hexdigest()
    pseudo_path = f"{path}#sql@{lit.line}"

    parsed = parse_file(lit.literal)
    fx = extract_parsed(
        parsed,
        repository=req.repository,
        root=req.root,
        environment=req.environment,
        path=pseudo_path,
        sha=sql_sha,
        adapter_version=_SQL_VERSION,
    )

    trans_keys = {
        e.identity.logical_key()
        for e in fx.entities
        if e.kind is DomainEntityKind.DATA_TRANSFORMATION
    }
    _remap_entities(sink, fx, trans_keys, path, sha, call_evi, call_span)
    sources, unresolved = _remap_relations(
        sink, fx, trans_keys, path, call_evi, call_span, t_ident
    )
    _flow_diagnostics(sink, fx, path, lit.line, parsed.statements)
    return sources, unresolved


def _role_map(
    sink: Sink, fx: FileExtraction
) -> dict[str, set[str]]:
    """Role do alvo por relação — chaveado por logical_key retaggada."""
    role_of: dict[str, set[str]] = {}
    for r in fx.relations:
        if r.to_identity is None:
            continue
        tk = sink.retag(r.to_identity).logical_key()
        bucket = role_of.setdefault(tk, set())
        if r.relation_type in _WRITE_REL:
            bucket.add("write")
        elif r.relation_type == DomainRelationKind.READS_FROM.value:
            bucket.add("read")
    return role_of


def _remap_entities(
    sink: Sink,
    fx: FileExtraction,
    trans_keys: set[str],
    path: str,
    sha: str,
    call_evi: int,
    call_span: SourceSpan | None,
) -> None:
    role_of = _role_map(sink, fx)
    for e in fx.entities:
        if e.identity.logical_key() in trans_keys:
            continue  # âncora interna do SQL — colapsa em t_ident
        ident = sink.retag(e.identity)
        attrs = dict(e.attributes)
        attrs["via"] = "spark.sql"
        sink.add_entity(
            ident, e.kind, e.name,
            path=path, span=call_span, content_hash=sha,
            attributes=attrs,
            roles=tuple(sorted(role_of.get(ident.logical_key(), ()))),
            evidence=(call_evi,),
        )


def _remap_relations(
    sink: Sink,
    fx: FileExtraction,
    trans_keys: set[str],
    path: str,
    call_evi: int,
    call_span: SourceSpan | None,
    t_ident: DomainIdentity,
) -> tuple[set[str], bool]:
    sources: set[str] = set()
    unresolved = False
    for r in fx.relations:
        if r.from_identity is None:
            # relação por símbolo — fora do modelo de linhagem daqui;
            # ancora honestamente no call-site
            from_i = t_ident
        elif r.from_identity.logical_key() in trans_keys:
            from_i = t_ident
        else:
            from_i = sink.retag(r.from_identity)
        to_i = sink.retag(r.to_identity) if r.to_identity else None
        if to_i is None:
            unresolved = True
        sink.add_rel(
            r.relation_type, from_i,
            path=path, to_i=to_i,
            to_text=r.to_unresolved if to_i is None else "",
            span=call_span, evidence=(call_evi,),
            resolution=r.resolution,
        )
        if (
            r.relation_type == DomainRelationKind.READS_FROM.value
            and to_i is not None
        ):
            sources.add(to_i.logical_key())
    return sources, unresolved


def _flow_diagnostics(
    sink: Sink, fx: FileExtraction, path: str, line: int,
    statements: object,
) -> None:
    for d in fx.diagnostics:
        sink.diag(
            d.code, d.severity,
            f"spark.sql@L{line}: {d.message}", path,
        )
    if not statements:
        sink.diag(
            "empty-sql", "info",
            f"spark.sql@L{line}: literal has no statements", path,
        )
