"""Extração de specs OpenAPI 3.x / Swagger 2.0 (lf-0114, Phase 6).

Consome o documento já parseado (dict do ``yaml_subset`` ou ``json``)
e emite:

- ``APIContract`` — um por arquivo de spec (identidade = path do spec);
- ``APIService`` — um por spec, identidade ``<spec_path>#<slug-título>``
  com ``server_url``/``base_path`` do primeiro ``servers[]`` (3.x) ou
  ``host``+``basePath``+``schemes`` (2.0);
- ``APIEndpoint`` — um por ``paths.<p>.<method>``, identidade
  ``<METHOD> <base_path><path>`` no namespace do serviço;
- relações ``DEFINES`` (contrato→serviço), ``CONTAINS`` (serviço→endpoint)
  e ``REFERENCES`` (endpoint→schema ``$ref`` — sempre ``unresolved``:
  schemas são texto de referência, não entidades deste domínio).

Escopo delimitado honesto: ``$ref`` de ``requestBody``/``responses``
(e ``items.$ref``) são registrados; ``allOf``/``oneOf`` aninhados,
callbacks, links e path-level ``parameters`` com body ficam fora —
documentado, nunca inventado.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping

from code_atlas.contracts.enums import (
    ConfidenceClass,
    EdgeKind,
    ResolutionStatus,
)
from code_atlas.domains.api import common
from code_atlas.domains.api.emit import Emit
from code_atlas.domains.api.yaml_subset import YamlValue
from code_atlas.domains.identity import DomainIdentity
from code_atlas.domains.model import DomainEntityKind
from code_atlas.parsers.contracts import SourceSpan

HTTP_METHODS = frozenset(
    {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
)
_PATH_ITEM_META = frozenset(
    {"parameters", "summary", "description", "servers", "$ref"}
)


def spec_flavor(doc: YamlValue) -> str | None:
    """``"openapi3"`` | ``"swagger2"`` | None conforme chaves de topo."""
    if not isinstance(doc, dict):
        return None
    ver = doc.get("openapi")
    if isinstance(ver, str | int | float) and str(ver).startswith("3."):
        return "openapi3"
    sw = doc.get("swagger")
    if isinstance(sw, str | int | float) and str(sw).startswith("2."):
        return "swagger2"
    return None


def _as_map(v: YamlValue) -> Mapping[str, YamlValue]:
    return v if isinstance(v, dict) else {}


def _as_list(v: YamlValue) -> list[YamlValue]:
    return v if isinstance(v, list) else []


def _as_str(v: YamlValue) -> str:
    if isinstance(v, str | int | float):
        return str(v)
    return ""


def _schema_refs(schema: YamlValue) -> list[str]:
    """``$ref`` direto + ``items.$ref`` — escopo delimitado documentado."""
    refs: list[str] = []
    m = _as_map(schema)
    ref = m.get("$ref")
    if isinstance(ref, str) and ref:
        refs.append(ref)
    items = _as_map(m.get("items"))
    iref = items.get("$ref")
    if isinstance(iref, str) and iref:
        refs.append(iref)
    return refs


def _media_schema_refs(content: YamlValue) -> list[str]:
    """Refs de ``content.<media-type>.schema`` (todas as media types)."""
    refs: list[str] = []
    for media in _as_map(content).values():
        refs.extend(_schema_refs(_as_map(media).get("schema")))
    return refs


def _operation_refs(op: Mapping[str, YamlValue], flavor: str) -> list[str]:
    """Refs de request/response da operação (3.x e 2.0)."""
    refs: list[str] = []
    if flavor == "openapi3":
        body = _as_map(op.get("requestBody"))
        refs.extend(_media_schema_refs(body.get("content")))
        for resp in _as_map(op.get("responses")).values():
            refs.extend(_media_schema_refs(_as_map(resp).get("content")))
    else:  # swagger2
        for param in _as_list(op.get("parameters")):
            pm = _as_map(param)
            if pm.get("in") == "body":
                refs.extend(_schema_refs(pm.get("schema")))
        for resp in _as_map(op.get("responses")).values():
            refs.extend(_schema_refs(_as_map(resp).get("schema")))
    return refs


def _server_info(doc: Mapping[str, YamlValue], flavor: str, sink: Emit,
                 path: str) -> tuple[str, str, int]:
    """``(server_url, base_path, servers_count)`` do primeiro servidor."""
    if flavor == "openapi3":
        servers = _as_list(doc.get("servers"))
        url = ""
        for srv in servers:
            u = _as_map(srv).get("url")
            if isinstance(u, str) and u:
                url = u
                break
        if url and "{" in url:
            sink.diag(
                "server-url-template", "info",
                f"server URL carries template vars: {url!r}", path,
            )
        base = common.url_path(url) if url else "/"
        return url, ("" if base == "/" else base), len(servers)
    host = _as_str(doc.get("host"))
    base = _as_str(doc.get("basePath")) or "/"
    scheme = ""
    schemes = _as_list(doc.get("schemes"))
    if schemes:
        scheme = _as_str(schemes[0])
    url = f"{scheme + '://' if scheme else ''}{host}{base}" if host else ""
    return url, ("" if base == "/" else base), 1 if host else 0


def extract_spec(
    *,
    sink: Emit,
    path: str,
    fmt: str,
    doc: YamlValue,
    flavor: str,
    key_line: Callable[[tuple[str, ...]], int | None],
    text: str,
    offsets: list[int],
    content_hash: str,
) -> None:
    """Extrai contrato/serviço/endpoints de um documento de spec.

    ``key_line`` é um callback ``(tuple[str, ...]) -> int | None`` que
    mapeia key-path YAML/JSON para linha 1-based; ``text``/``offsets``
    sustentam ``common.line_span`` (spans honestos por linha).
    """
    top = _as_map(doc)
    info = _as_map(top.get("info"))
    title = _as_str(info.get("title"))
    spec_version = _as_str(info.get("version"))
    stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    svc_name = common.slugify(title) or common.slugify(stem) or "service"
    server_url, base_path, n_servers = _server_info(top, flavor, sink, path)
    if n_servers > 1:
        sink.diag(
            "multi-server", "info",
            f"{n_servers} servers declared — first used for base_path",
            path,
        )

    def span_at(ln: int | None) -> SourceSpan | None:
        return common.line_span(text, offsets, ln) if ln else None

    # APIContract — um por arquivo de spec.
    contract_line = key_line(("openapi",)) or key_line(("swagger",)) or 1
    contract = sink.entity(
        kind=DomainEntityKind.API_CONTRACT,
        name=title or stem,
        qualified_name=path,
        path=path,
        span=span_at(contract_line),
        content_hash=content_hash,
        attributes={
            "format": fmt,
            "spec_version": spec_version,
            "api_version": _as_str(
                top.get("openapi", top.get("swagger", ""))
            ),
        },
        evidence=(sink.evi(path, content_hash, span_at(contract_line)),),
    )
    sink.bump("api_contracts")

    # APIService — identidade = spec file + título slug.
    svc_qname = f"{path}#{svc_name}"
    service = sink.entity(
        kind=DomainEntityKind.API_SERVICE,
        name=title or svc_name,
        qualified_name=svc_qname,
        path=path,
        span=span_at(key_line(("info", "title")) or contract_line),
        content_hash=content_hash,
        attributes={
            "title": title,
            "version": spec_version,
            "server_url": server_url,
            "base_path": base_path,
            "servers_count": str(n_servers),
            "source": "openapi-spec",
            "spec": path,
        },
        evidence=(sink.evi(path, content_hash, span_at(contract_line)),),
    )
    sink.bump("api_services")
    ev_def = sink.evi(path, content_hash, span_at(contract_line))
    sink.relation(
        relation_type=EdgeKind.DEFINES,
        path=path,
        from_identity=contract,
        to_identity=service,
        resolution=sink.resolution(
            ResolutionStatus.RESOLVED, ConfidenceClass.SYNTACTIC,
            target=service,
        ),
        span=span_at(contract_line),
        evidence=(ev_def,),
    )

    paths = _as_map(top.get("paths"))
    if not paths:
        sink.diag(
            "empty-paths", "info",
            "spec declares no paths entries", path,
        )
    for raw_path, item in paths.items():
        if not raw_path.startswith("/"):
            if not raw_path.startswith("x-"):
                sink.diag(
                    "path-key-ignored", "info",
                    f"paths key not a route: {raw_path!r}", path,
                )
            continue
        item_map = _as_map(item)
        for method, op in item_map.items():
            if method.lower() in _PATH_ITEM_META or method.startswith("x-"):
                continue
            if method.lower() not in HTTP_METHODS:
                sink.diag(
                    "path-key-ignored", "info",
                    f"path item key not an HTTP method: {method!r}", path,
                )
                continue
            _emit_operation(
                sink=sink,
                path=path,
                flavor=flavor,
                raw_path=raw_path,
                method=method,
                op_map=_as_map(op),
                base_path=base_path,
                service=service,
                svc_qname=svc_qname,
                ln=key_line(("paths", raw_path, method)),
                span_at=span_at,
                content_hash=content_hash,
            )


def _emit_operation(
    *,
    sink: Emit,
    path: str,
    flavor: str,
    raw_path: str,
    method: str,
    op_map: Mapping[str, YamlValue],
    base_path: str,
    service: DomainIdentity,
    svc_qname: str,
    ln: int | None,
    span_at: Callable[[int | None], SourceSpan | None],
    content_hash: str,
) -> None:
    """Emite um APIEndpoint + CONTAINS + REFERENCES para uma operação."""
    m_upper = method.upper()
    full_path = common.join_paths(base_path, raw_path)
    op_id = _as_str(op_map.get("operationId"))
    attrs = {
        "method": m_upper,
        "path": raw_path,
        "full_path": full_path,
        "base_path": base_path,
        "source": "openapi-spec",
        "spec": path,
    }
    if op_id:
        attrs["operation_id"] = op_id
    tags = [t for t in _as_list(op_map.get("tags")) if isinstance(t, str)]
    if tags:
        attrs["tags"] = "|".join(tags)
    span = span_at(ln)
    ep = sink.endpoint(
        name=op_id or f"{m_upper} {raw_path}",
        qualified_name=f"{m_upper} {full_path}",
        namespace=svc_qname,
        full_path=full_path,
        path=path,
        span=span,
        content_hash=content_hash,
        attributes=attrs,
        evidence=(sink.evi(path, content_hash, span),),
    )
    sink.bump("api_endpoints")
    sink.relation(
        relation_type=EdgeKind.CONTAINS,
        path=path,
        from_identity=service,
        to_identity=ep,
        resolution=sink.resolution(
            ResolutionStatus.RESOLVED, ConfidenceClass.SYNTACTIC,
            target=ep,
        ),
        span=span,
        evidence=(sink.evi(path, content_hash, span),),
    )
    for ref in dict.fromkeys(_operation_refs(op_map, flavor)):
        sink.relation(
            relation_type=EdgeKind.REFERENCES,
            path=path,
            from_identity=ep,
            to_unresolved=ref,
            resolution=sink.resolution(
                ResolutionStatus.UNRESOLVED,
                ConfidenceClass.SYNTACTIC,
                unresolved_text=ref,
            ),
            span=span,
            evidence=(sink.evi(path, content_hash, span),),
        )
        sink.bump("schema_refs")


def make_key_line(
    key_lines: Mapping[tuple[str, ...], int],
) -> Callable[[tuple[str, ...]], int | None]:
    """Callback ``key_line`` para YAML — lookup direto do mapa do parser."""
    def _lookup(path: tuple[str, ...]) -> int | None:
        return key_lines.get(path)
    return _lookup


def _json_find(
    lines: list[str], key: str, start: int, end: int
) -> int | None:
    pat = re.compile(rf'"{re.escape(key)}"\s*:')
    for i in range(start, min(end, len(lines))):
        if pat.search(lines[i]):
            return i + 1
    return None


def _json_paths_lookup(
    lines: list[str], path: tuple[str, ...]
) -> int | None:
    start = _json_find(lines, "paths", 0, len(lines))
    if start is None:
        return None
    pl = _json_find(lines, path[1], start, len(lines))
    if pl is None or len(path) == 2:
        return pl
    return _json_find(lines, path[2], pl, pl + 400)


def make_json_key_line(
    text: str,
) -> Callable[[tuple[str, ...]], int | None]:
    """``key_line`` aproximado para JSON — localiza a linha da chave.

    Busca textual honesta: para ``("paths", p, m)`` acha a linha de
    ``"<p>"`` após ``"paths"`` e a primeira ``"<m>"`` seguinte; para
    chaves de topo, a primeira linha ``"<key>"``. Nunca inventa — None
    quando não encontrado (span cai para None, evidência segue válida).
    """
    lines = text.splitlines()

    def _lookup(path: tuple[str, ...]) -> int | None:
        if not path:
            return None
        if len(path) == 1:
            return _json_find(lines, path[0], 0, len(lines))
        if path[0] == "paths" and len(path) >= 2:
            return _json_paths_lookup(lines, path)
        return None

    return _lookup
