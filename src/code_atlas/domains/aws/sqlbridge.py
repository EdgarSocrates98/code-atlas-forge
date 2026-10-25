"""Ponte ``domains.sql`` → ``domains.aws`` (lf-0113, Cycle 7 Phase 5).

Reuso **como biblioteca** — nunca reemissão sob o domínio ``sql``:

- ``parse_file`` + ``extract_parsed`` produzem fatos de linhagem sobre
  texto SQL (arquivo ``.sql`` Athena inteiro ou literal ``spark.sql``);
- cada identidade emitida é re-marcada para ``domain=aws-data``
  preservando ``catalog``/``namespace``/``qualified_name`` — o
  produtor do fato é o adapter aws (o contrato de validação exige
  ``identity.domain == adapter.domain_id``); a proveniência do parser
  fica em ``evidence.notes`` (``via=domain:sql``);
- literais embutidos em ``.py`` carregam spans relativos ao literal —
  ``span_remap`` desloca para offsets reais do arquivo quando o corpo
  do literal é contíguo no fonte; quando não é (escapes/concat),
  todos os spans caem para o span do literal (honesto, nunca errado);
- ``USE <catalog>.<db>;`` sintético só é prefixado quando o contexto
  foi **declarado no artefato** (ex.: ``QueryExecutionContext`` de
  ``start_query_execution``) — contexto inventado nunca entra;
- literais ``s3://`` dentro do SQL (``LOCATION``, ``OutputLocation``)
  viram entidades ``data_table`` S3 + ``REFERENCES`` a partir da
  tabela-alvo do statement (ou do ``data_transformation`` do
  statement quando não há alvo nomeado).

Resolução cross-domain com o domínio ``sql`` é trabalho de lf-0117 —
aqui só se re-marca o que o parser provou no texto.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from code_atlas.contracts.enums import ConfidenceClass
from code_atlas.domains.aws.common import (
    ICEBERG_META,
    byte_span,
    parse_s3_uri,
    s3_uri,
)
from code_atlas.domains.aws.emit import ADAPTER_DOMAIN, RESOLVER_ID, Sink
from code_atlas.domains.contracts import ExtractedDomainEntity
from code_atlas.domains.identity import DomainIdentity
from code_atlas.domains.model import DomainEntityKind
from code_atlas.domains.resolution import DomainResolution
from code_atlas.domains.sql.extract import FileExtraction, extract_parsed
from code_atlas.domains.sql.nodes import SCreateTable, SInsert, Statement
from code_atlas.domains.sql.parser import parse_file
from code_atlas.parsers.contracts import SourceSpan

_S3_LIT_RE = re.compile(r"""['"](s3://[^'"]+)['"]""")

SpanRemap = Callable[[SourceSpan | None], SourceSpan | None]


@dataclass(frozen=True)
class SqlFragment:
    """Resultado re-marcado de um fragmento SQL."""

    transform_idents: tuple[DomainIdentity, ...]
    """``data_transformation`` emitidas (âncoras do fragmento)."""


def _map_ident(transform_tag: str, path: str, ident: DomainIdentity) -> DomainIdentity:
    """Re-marca identidade ``sql`` → ``aws-data`` (mesmos segmentos).

    ``transform_tag`` (ex.: ``L42``) desambigua literais embutidos no
    mesmo arquivo: ``path#3`` vira ``path@L42#3``.
    """
    qname = ident.qualified_name
    if transform_tag and qname.startswith(f"{path}#"):
        qname = f"{path}@{transform_tag}#" + qname[len(path) + 1 :]
    return DomainIdentity(
        domain=ADAPTER_DOMAIN,
        repository=ident.repository,
        qualified_name=qname,
        root=ident.root,
        catalog=ident.catalog,
        namespace=ident.namespace,
        environment=ident.environment,
        snapshot=ident.snapshot,
    )


def _table_ident(
    sink: Sink, parts: tuple[str, ...], ctx_catalog: str, ctx_schema: str
) -> DomainIdentity:
    """Mesma decomposição de ``_FileCtx.table_ident`` do adapter sql."""
    if len(parts) >= 3:
        catalog, namespace, name = (parts[0], ".".join(parts[1:-1]), parts[-1])
    elif len(parts) == 2:
        catalog, namespace, name = ctx_catalog, parts[0], parts[1]
    else:
        catalog, namespace, name = ctx_catalog, ctx_schema, parts[0]
    return sink.ident(name, catalog=catalog, namespace=namespace)


def _transform_ident(sink: Sink, path: str, stmt_i: int, transform_tag: str) -> DomainIdentity:
    qname = f"{path}@{transform_tag}#{stmt_i}" if transform_tag else f"{path}#{stmt_i}"
    return sink.ident(qname)


def _char_to_byte(text: str, char_pos: int) -> int:
    return len(text[:char_pos].encode("utf-8"))


def _span_in_text(text: str, char_start: int, char_end: int) -> SourceSpan:
    """Span byte-accurate dentro do texto SQL (linha/col próprias)."""
    starts = [0]
    off = 0
    for ch in text:
        off += len(ch.encode("utf-8"))
        if ch == "\n":
            starts.append(off)
    b0 = _char_to_byte(text, char_start)
    b1 = _char_to_byte(text, char_end)
    return byte_span(tuple(starts), b0, b1)


def s3_entity(
    sink: Sink,
    uri: str,
    *,
    path: str,
    sha: str,
    span: SourceSpan | None,
    evi: int,
) -> DomainIdentity | None:
    """Entidade ``data_table`` para uma URI ``s3://`` literal estática.

    ``None`` quando ``uri`` é template/dinâmica ou malformada — o
    caller decide entre diagnóstico e ``to_unresolved``.
    """
    parsed = parse_s3_uri(uri)
    if parsed is None:
        return None
    bucket, key = parsed
    uri_norm = s3_uri(bucket, key)
    return sink.entity(
        kind=DomainEntityKind.DATA_TABLE,
        name=uri_norm,
        qualified_name=uri_norm,
        path=path,
        span=span,
        content_hash=sha,
        attributes={
            "storage": "s3-path",
            "uri": uri_norm,
            "bucket": bucket,
        },
        evidence=(evi,),
    )


def _emit_s3_literals(
    sink: Sink,
    *,
    sql_text: str,
    statements: list[Statement],
    path: str,
    sha: str,
    transform_tag: str,
    span_remap: SpanRemap,
    ctx_catalog: str,
    ctx_schema: str,
    fallback_anchor: DomainIdentity | None,
) -> None:
    """Literais ``s3://`` no SQL → entidade + ``REFERENCES`` da âncora.

    Âncora = tabela-alvo do statement que contém o literal quando ela
    foi emitida; senão a transformação ``path#i``; senão
    ``fallback_anchor`` (ex.: job dono do literal embutido).
    """
    for m in _S3_LIT_RE.finditer(sql_text):
        uri = m.group(1)
        bpos = _char_to_byte(sql_text, m.start())
        anchor = fallback_anchor
        for i, st in enumerate(statements):
            sp = st.span
            if sp is not None and sp.start_byte <= bpos <= sp.end_byte:
                anchor = _stmt_anchor(
                    sink,
                    i,
                    st,
                    path,
                    transform_tag,
                    ctx_catalog,
                    ctx_schema,
                    fallback_anchor,
                )
                break
        if anchor is None:
            continue
        fspan = span_remap(_span_in_text(sql_text, m.start(1), m.end(1)))
        evi = sink.evi(path, sha, fspan, notes="s3-literal")
        ident = s3_entity(sink, uri, path=path, sha=sha, span=fspan, evi=evi)
        if ident is None:
            sink.diag(
                "dynamic-s3-uri",
                "warning",
                "s3 URI is a template/dynamic — never an entity",
                path,
            )
            continue
        sink.rel(
            "REFERENCES",
            path,
            anchor,
            to_identity=ident,
            span=fspan,
            evidence=(evi,),
        )
        sink.bump("s3_paths")


def _stmt_anchor(
    sink: Sink,
    stmt_i: int,
    st: Statement,
    path: str,
    transform_tag: str,
    ctx_catalog: str,
    ctx_schema: str,
    fallback: DomainIdentity | None,
) -> DomainIdentity | None:
    """Tabela declarada/alvo do statement, senão a transformação ``#i``."""
    target_parts: tuple[str, ...] | None = None
    if isinstance(st, SCreateTable) and st.name is not None:
        target_parts = st.name.parts
    elif isinstance(st, SInsert) and st.target is not None:
        target_parts = st.target.parts
    if target_parts is not None:
        ident = _table_ident(sink, target_parts, ctx_catalog, ctx_schema)
        if ident.logical_key() in sink.entities:
            return ident
    tident = _transform_ident(sink, path, stmt_i, transform_tag)
    if tident.logical_key() in sink.entities:
        return tident
    return fallback


def _map_res(
    transform_tag: str, path: str, res: DomainResolution | None
) -> DomainResolution | None:
    """Re-marca uma ``DomainResolution`` sql → aws-data."""
    if res is None:
        return None
    return DomainResolution(
        status=res.status,
        confidence_class=res.confidence_class,
        resolver=RESOLVER_ID,
        target=(_map_ident(transform_tag, path, res.target) if res.target is not None else None),
        candidates=tuple(_map_ident(transform_tag, path, c) for c in res.candidates),
        unresolved_text=res.unresolved_text,
    )


def _entity_kind(sink: Sink, e: ExtractedDomainEntity) -> tuple[DomainEntityKind, dict[str, str]]:
    """Promoção ``data_table`` → ``iceberg_table`` quando provada."""
    kind = e.kind
    extra: dict[str, str] = {}
    if kind is not DomainEntityKind.DATA_TABLE:
        return kind, extra
    # meta-tabela ``…t.snapshots`` ou tabela sob catálogo iceberg
    if e.name.lower() in ICEBERG_META and e.identity.namespace:
        return DomainEntityKind.ICEBERG_TABLE, {"iceberg_meta_table": e.name.lower()}
    cat_ent = sink.catalogs.get(e.identity.catalog)
    prev = sink.entities.get(cat_ent.logical_key()) if cat_ent is not None else None
    if prev is not None and prev.attributes.get("engine") == "iceberg":
        return DomainEntityKind.ICEBERG_TABLE, extra
    return kind, extra


def _merge_sql_entities(
    sink: Sink,
    fx: FileExtraction,
    *,
    transform_tag: str,
    path: str,
    remap: SpanRemap,
    ev_map: dict[int, int],
) -> list[DomainIdentity]:
    """Re-marca entidades sql → aws-data; retorna transformações."""
    transforms: list[DomainIdentity] = []
    for e in fx.entities:
        ident = _map_ident(transform_tag, path, e.identity)
        kind, attrs_extra = _entity_kind(sink, e)
        if kind is DomainEntityKind.DATA_TRANSFORMATION:
            transforms.append(ident)
        # atributos que citam identidades (ex.: ``table: sql:db.t``)
        # re-marcados para o domínio produtor — mesmo fato, novo dono
        attrs = {
            k: (
                f"{ADAPTER_DOMAIN}:{v[len('sql') + 1 :]}"
                if isinstance(v, str) and v.startswith("sql:")
                else v
            )
            for k, v in e.attributes.items()
        }
        attrs.update(attrs_extra)
        sink.entity_for(
            ident,
            kind=kind,
            name=e.name,
            path=e.path,
            span=remap(e.span),
            content_hash=e.content_hash,
            attributes=attrs,
            evidence=tuple(ev_map[i] for i in e.evidence if i in ev_map),
        )
    return transforms


def _merge_sql_relations(
    sink: Sink,
    fx: FileExtraction,
    *,
    transform_tag: str,
    path: str,
    remap: SpanRemap,
    ev_map: dict[int, int],
) -> None:
    """Re-marca relações sql → aws-data (mesmo vocabulário fechado)."""
    for r in fx.relations:
        if r.from_identity is None:
            # o extractor sql nunca emite from_symbol; se ocorrer,
            # registra-se e segue — nunca crash.
            sink.diag(
                "unsupported-relation",
                "warning",
                f"sql relation with from_symbol={r.from_symbol!r} skipped — no identity endpoint",
                path,
            )
            continue
        res = _map_res(transform_tag, path, r.resolution)
        sink.rel(
            r.relation_type,
            r.path,
            _map_ident(transform_tag, path, r.from_identity),
            to_identity=(
                _map_ident(transform_tag, path, r.to_identity)
                if r.to_identity is not None
                else None
            ),
            to_unresolved=r.to_unresolved or (res.unresolved_text if res is not None else ""),
            status=(res.status if res is not None else None),
            confidence=(res.confidence_class if res is not None else ConfidenceClass.SYNTACTIC),
            span=remap(r.span),
            evidence=tuple(ev_map[i] for i in r.evidence if i in ev_map),
        )


def extract_sql_fragment(
    sink: Sink,
    *,
    sql_text: str,
    path: str,
    sha: str,
    transform_tag: str = "",
    span_remap: SpanRemap | None = None,
    use_catalog: str = "",
    use_schema: str = "",
    fallback_anchor: DomainIdentity | None = None,
) -> SqlFragment:
    """Parseia + extrai + re-marca um fragmento SQL no ``Sink``.

    ``use_catalog``/``use_schema`` entram via ``USE`` sintético —
    somente quando declarados no artefato. ``span_remap`` desloca spans
    relativos ao fragmento para spans reais do arquivo.
    """
    remap = span_remap or (lambda s: s)
    prefix = ""
    if use_catalog or use_schema:
        use_name = ".".join(p for p in (use_catalog, use_schema) if p)
        if use_name:
            prefix = f"USE {use_name};\n"
    full_text = prefix + sql_text
    parsed = parse_file(full_text)
    fx = extract_parsed(
        parsed,
        repository=sink.repository,
        root=sink.root,
        environment=sink.environment,
        path=path,
        sha=sha,
        adapter_version=sink.version,
    )
    # merge no sink — entidades/relações re-marcadas para aws-data
    ev_map: dict[int, int] = {}
    for i, ev in enumerate(fx.evidence):
        notes = f"{ev.notes};via=domain:sql" if ev.notes else ("via=domain:sql")
        ev_map[i] = sink.evi(ev.path, ev.content_hash, remap(ev.span), notes=notes)
    transforms = _merge_sql_entities(
        sink, fx, transform_tag=transform_tag, path=path, remap=remap, ev_map=ev_map
    )
    _merge_sql_relations(
        sink, fx, transform_tag=transform_tag, path=path, remap=remap, ev_map=ev_map
    )
    for d in fx.diagnostics:
        sink.diag(d.code, d.severity, d.message, path=d.path or path)
    for k, v in fx.stats.items():
        sink.bump(f"sql_{k}", v)

    _emit_s3_literals(
        sink,
        sql_text=full_text,
        statements=parsed.statements,
        path=path,
        sha=sha,
        transform_tag=transform_tag,
        span_remap=remap,
        ctx_catalog=use_catalog,
        ctx_schema=use_schema,
        fallback_anchor=fallback_anchor,
    )
    sink.bump("sql_fragments")
    return SqlFragment(transform_idents=tuple(transforms))


def literal_span_remap(
    *,
    file_text: str,
    line_starts: tuple[int, ...],
    body_char_start: int,
    contiguous: bool,
    fallback: SourceSpan | None,
) -> SpanRemap:
    """Remap de spans de literal embutido → offsets reais do arquivo.

    ``contiguous=True`` quando o corpo decodificado do literal é uma
    fatia literal do fonte (sem escapes) — então spans deslocam por
    ``body_char_start``; senão todo span cai para ``fallback`` (o span
    do literal) — nunca um offset errado.
    """
    body_byte_start = _char_to_byte(file_text, body_char_start)

    def _remap(span: SourceSpan | None) -> SourceSpan | None:
        if span is None:
            return None
        if not contiguous:
            return fallback
        return byte_span(
            line_starts,
            body_byte_start + span.start_byte,
            body_byte_start + span.end_byte,
        )

    return _remap
