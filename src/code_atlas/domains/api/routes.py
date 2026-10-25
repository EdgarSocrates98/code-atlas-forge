"""Rotas declaradas em código — FastAPI (Python) e Express (TS/JS) (lf-0114).

FastAPI (.py): consome artefatos de AST reais — ``ParseResult`` do
``ParseEngine`` (Tree-sitter vive atrás da fronteira ``parsers/``).
Decoradores ``@app.get("/x")``/``@router.post(...)`` chegam como texto de
decorator nos símbolos (``decorated_definition``); receivers
``FastAPI()``/``APIRouter(prefix=...)`` e ``include_router`` chegam como
referências ``call`` com span real — argumentos são fatiados do código
fonte na posição do callee (nunca regex solto em texto bruto).

Express (.ts/.tsx): mesmo pipeline de AST; convenção de receiver
(``app``/``router``/vars de ``express()``/``Router()``) → confiança
``heuristic`` declarada. ``.js``/``.jsx``/``.mjs``/``.cjs`` não têm
adaptador Tree-sitter neste repo — regex-static honesto por linha.

Relações emitidas:

- ``APIService --CONTAINS--> APIEndpoint`` (resolvido internamente);
- ``<handler symbol> --DEFINES--> APIEndpoint`` — ponte código→domínio
  (``EXPOSES`` não consta do vocabulário fechado de ``DomainRelationKind``;
  ``DEFINES`` é o membro honesto: o handler literalmente define a rota);
- ``<app service> --CONTAINS--> <router service>`` para
  ``include_router``/``use`` observados no mesmo arquivo.

Limitações declaradas: prefixo de ``include_router`` só se aplica quando
o router var é declarado no mesmo arquivo (cross-file → diagnóstico);
paths de rota não-literais (f-string, variável) → diagnóstico
``dynamic-route-path`` e nenhum endpoint inventado.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from code_atlas.contracts.enums import (
    ConfidenceClass,
    EdgeKind,
    ResolutionStatus,
)
from code_atlas.domains.api import common
from code_atlas.domains.api.emit import Emit
from code_atlas.domains.identity import DomainIdentity
from code_atlas.domains.model import DomainEntityKind
from code_atlas.parsers.contracts import ParseResult, SourceSpan

HTTP_METHODS = frozenset(
    {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
)
_ROUTE_DEC = re.compile(
    r"^([\w.]+)\.(get|put|post|delete|options|head|patch|trace"
    r"|route|api_route|websocket|webhooks)\s*\("
)
_PY_CTOR = re.compile(r"^(?:[\w.]+\.)?(FastAPI|APIRouter|Flask|Blueprint)$")
_PY_VAR_LHS = re.compile(r"^\s*([A-Za-z_]\w*)\s*(?::[^=]+)?=")
_TS_CTOR = re.compile(r"^(?:express\.Router|express|Router)$")
_TS_VAR_LHS = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*="
)
_SIMPLE_RECV = re.compile(r"^[A-Za-z_$][\w$]*$")
_JS_ROUTE = re.compile(
    r"^\s*([A-Za-z_$][\w$]*)\."
    r"(get|put|post|delete|options|head|patch|all|use)\s*\("
)


@dataclass(frozen=True)
class _Route:
    receiver: str
    method: str          # UPPER ou "ANY"
    path: str
    span: SourceSpan | None
    handler: str         # qname do símbolo que define a rota


@dataclass
class _ServiceInfo:
    kind: str            # "app" | "router" | "unknown"
    prefix: str          # prefixo declarado (APIRouter/root_path)
    mount: str           # prefixo de mount (include_router/app.use)
    declared: bool
    span: SourceSpan | None


def _args_at(text: str, span_end_byte: int) -> str | None:
    """Argumentos ``(...)`` logo após o fim do callee (byte offset)."""
    pos = common.byte_to_char(text, span_end_byte)
    return common.args_after(text, pos)


def _line_at(text: str, lineno: int) -> str:
    lines = text.splitlines()
    return lines[lineno - 1] if 1 <= lineno <= len(lines) else ""


def _method_lit(token: str, *, lang: str) -> str | None:
    lit = common.parse_str_literal(token, lang=lang)
    if lit is None or lit.dynamic:
        return None
    m = lit.value.strip().upper()
    return m if m in {x.upper() for x in HTTP_METHODS} else None


def _first_lit(args: list[str], *, lang: str) -> common.StrLiteral | None:
    if not args:
        return None
    return common.parse_str_literal(args[0], lang=lang)


# -- FastAPI / Python ------------------------------------------------------


def _py_receiver_maps(
    sink: Emit, path: str, text: str, result: ParseResult
) -> tuple[dict[str, _ServiceInfo], list[tuple[str, str, str]]]:
    """Vars ``X = FastAPI()/APIRouter()`` e ``X.include_router(Y, ...)``."""
    services: dict[str, _ServiceInfo] = {}
    includes: list[tuple[str, str, str]] = []  # (app_var, router_var, prefix)
    for ref in result.references:
        if ref.context != "call":
            continue
        callee = ref.name.strip()
        m = _PY_CTOR.match(callee)
        if m:
            lhs = _PY_VAR_LHS.match(_line_at(text, ref.span.start_line))
            if not lhs:
                continue
            args = common.split_args(_args_at(text, ref.span.end_byte) or "")
            kw = "prefix" if m.group(1) in ("APIRouter", "Blueprint") else "root_path"
            prefix_lit = common.parse_str_literal(
                common.kwarg(args, kw) or "", lang="py"
            )
            prefix = (
                prefix_lit.value if prefix_lit and not prefix_lit.dynamic else ""
            )
            var = lhs.group(1)
            services[var] = _ServiceInfo(
                kind="router" if m.group(1) in ("APIRouter", "Blueprint") else "app",
                prefix=prefix,
                mount="",
                declared=True,
                span=ref.span,
            )
            continue
        if callee.endswith(".include_router"):
            app_var = callee[: -len(".include_router")].rsplit(".", 1)[-1]
            args_text = _args_at(text, ref.span.end_byte) or ""
            args = common.split_args(args_text)
            if not args:
                continue
            router_var = args[0].strip()
            if not _SIMPLE_RECV.match(router_var):
                continue
            prefix_lit = common.parse_str_literal(
                common.kwarg(args, "prefix") or "", lang="py"
            )
            prefix = (
                prefix_lit.value if prefix_lit and not prefix_lit.dynamic else ""
            )
            includes.append((app_var, router_var, prefix))
    return services, includes


def _py_routes(result: ParseResult) -> tuple[list[_Route], int]:
    """Rotas a partir dos decorators reais extraídos pelo Tree-sitter."""
    routes: list[_Route] = []
    skipped_nonhttp = 0
    for sym in result.symbols:
        for dec in sym.decorators:
            m = _ROUTE_DEC.match(dec.strip())
            if not m:
                continue
            recv, verb = m.group(1), m.group(2)
            if verb in ("websocket", "webhooks"):
                skipped_nonhttp += 1
                continue
            open_paren = dec.find("(", m.end() - 1)
            args = common.split_args(common.scan_args(dec, open_paren) or "")
            lit = _first_lit(args, lang="py")
            if lit is None or lit.dynamic:
                routes.append(
                    _Route(recv, "?", "", sym.span, sym.qualified_name)
                )
                continue
            if verb in ("route", "api_route"):
                methods_raw = common.kwarg(args, "methods") or ""
                methods = re.findall(r"['\"](\w+)['\"]", methods_raw)
                if not methods:
                    methods = ["ANY"]
                for mm in methods:
                    routes.append(
                        _Route(
                            recv, mm.upper(), lit.value,
                            sym.span, sym.qualified_name,
                        )
                    )
                continue
            routes.append(
                _Route(recv, verb.upper(), lit.value,
                       sym.span, sym.qualified_name)
            )
    return routes, skipped_nonhttp


def extract_py_routes(
    *,
    sink: Emit,
    path: str,
    text: str,
    result: ParseResult,
    module: str,
    content_hash: str,
) -> None:
    """FastAPI: decorators + receivers via AST real — ``syntactic``."""
    services, includes = _py_receiver_maps(sink, path, text, result)
    routes, skipped_nonhttp = _py_routes(result)
    if skipped_nonhttp:
        sink.diag(
            "non-http-decorator", "info",
            f"{skipped_nonhttp} websocket/webhooks decorator(s) skipped "
            f"in {path} — not HTTP API endpoints",
            path,
        )
    _emit_routes(
        sink=sink, path=path, text=text, module=module,
        content_hash=content_hash, services=services, includes=includes,
        routes=routes, framework="fastapi",
        confidence=ConfidenceClass.SYNTACTIC,
        source="fastapi-decorator",
    )


# -- Express / TypeScript -----------------------------------------------------


def _ts_receiver_maps(
    text: str, result: ParseResult
) -> tuple[dict[str, _ServiceInfo], list[tuple[str, str, str]]]:
    """``X = express()/Router()`` e ``X.use("/p", Y)`` — spans reais."""
    services: dict[str, _ServiceInfo] = {}
    mounts: list[tuple[str, str, str]] = []  # (app_var, router_var, prefix)
    for ref in result.references:
        if ref.context != "call":
            continue
        callee = ref.name.strip()
        if _TS_CTOR.match(callee):
            lhs = _TS_VAR_LHS.match(_line_at(text, ref.span.start_line))
            if not lhs:
                continue
            kind = "router" if callee.endswith("Router") else "app"
            services[lhs.group(1)] = _ServiceInfo(
                kind=kind, prefix="", mount="",
                declared=True, span=ref.span,
            )
            continue
        if callee.endswith(".use"):
            recv = callee[: -len(".use")]
            if not _SIMPLE_RECV.match(recv):
                continue
            args = common.split_args(_args_at(text, ref.span.end_byte) or "")
            if len(args) < 2:
                continue
            mount_lit = common.parse_str_literal(args[0], lang="ts")
            router_var = args[1].strip()
            if (
                mount_lit and not mount_lit.dynamic
                and _SIMPLE_RECV.match(router_var)
            ):
                mounts.append((recv, router_var, mount_lit.value))
            continue
    return services, mounts


def _ts_routes(
    text: str,
    result: ParseResult,
    module: str,
    services: dict[str, _ServiceInfo],
) -> list[_Route]:
    routes: list[_Route] = []
    for ref in result.references:
        if ref.context != "call":
            continue
        callee = ref.name.strip()
        base, _, verb = callee.rpartition(".")
        if verb not in HTTP_METHODS and verb != "all":
            continue
        if not _SIMPLE_RECV.match(base):
            continue
        if base not in services and base not in ("app", "router"):
            continue
        args = common.split_args(_args_at(text, ref.span.end_byte) or "")
        lit = _first_lit(args, lang="ts")
        scope = ".".join(ref.scope_path)
        caller = f"{module}.{scope}" if scope else module
        if lit is None or lit.dynamic:
            routes.append(_Route(base, "?", "", ref.span, caller))
            continue
        method = "ANY" if verb == "all" else verb.upper()
        routes.append(_Route(base, method, lit.value, ref.span, caller))
    return routes


def extract_ts_routes(
    *,
    sink: Emit,
    path: str,
    text: str,
    result: ParseResult,
    module: str,
    content_hash: str,
) -> None:
    """Express em .ts/.tsx: AST real + convenção de receiver — ``heuristic``."""
    services, mounts = _ts_receiver_maps(text, result)
    routes = _ts_routes(text, result, module, services)
    _emit_routes(
        sink=sink, path=path, text=text, module=module,
        content_hash=content_hash, services=services, includes=mounts,
        routes=routes, framework="express",
        confidence=ConfidenceClass.HEURISTIC,
        source="express-literal",
    )


# -- Express / JavaScript regex-static (sem adaptador Tree-sitter) -----------


def extract_js_routes(
    *,
    sink: Emit,
    path: str,
    text: str,
    module: str,
    content_hash: str,
) -> None:
    """Regex-static honesto para .js — sem AST; sempre ``heuristic``."""
    services: dict[str, _ServiceInfo] = {}
    mounts: list[tuple[str, str, str]] = []
    routes: list[_Route] = []
    lines = text.splitlines()
    byte_offs = common.byte_offsets(text)
    for lineno, line in enumerate(lines, 1):
        vm = _TS_VAR_LHS.match(line)
        ctor = re.search(
            r"=\s*(?:[\w.]+\.)?(express\.Router|express|Router)\s*\(", line
        )
        if vm and ctor:
            services[vm.group(1)] = _ServiceInfo(
                kind="router" if ctor.group(1).endswith("Router") else "app",
                prefix="", mount="", declared=True,
                span=common.line_span(text, byte_offs, lineno),
            )
            continue
        rm = _JS_ROUTE.match(line)
        if not rm:
            continue
        recv, verb = rm.group(1), rm.group(2).lower()
        args = common.split_args(common.scan_args(line, rm.end() - 1) or "")
        span = common.line_span(text, byte_offs, lineno)
        if verb == "use":
            if len(args) >= 2:
                mount_lit = common.parse_str_literal(args[0], lang="ts")
                router_var = args[1].strip()
                if (
                    mount_lit and not mount_lit.dynamic
                    and _SIMPLE_RECV.match(router_var)
                ):
                    mounts.append((recv, router_var, mount_lit.value))
            continue
        lit = _first_lit(args, lang="ts")
        if lit is None or lit.dynamic:
            routes.append(_Route(recv, "?", "", span, module))
            continue
        method = "ANY" if verb == "all" else verb.upper()
        routes.append(_Route(recv, method, lit.value, span, module))
    _emit_routes(
        sink=sink, path=path, text=text, module=module,
        content_hash=content_hash, services=services, includes=mounts,
        routes=routes, framework="express",
        confidence=ConfidenceClass.HEURISTIC,
        source="express-regex",
    )


# -- emissão compartilhada ----------------------------------------------------


def _mount_prefixes(
    *,
    sink: Emit,
    path: str,
    services: dict[str, _ServiceInfo],
    includes: list[tuple[str, str, str]],
) -> dict[str, str]:
    """``router_var`` → prefixo de mount (primeiro observado no arquivo)."""
    mount_of: dict[str, list[tuple[str, str]]] = {}
    for app_var, router_var, prefix in includes:
        mount_of.setdefault(router_var, []).append((app_var, prefix))
        if router_var not in services:
            sink.diag(
                "cross-file-router-prefix", "info",
                f"{router_var!r} mounted but not declared in {path} — "
                "prefix may be incomplete",
                path,
            )
    mount_str: dict[str, str] = {}
    for router_var, entries in mount_of.items():
        if len(entries) > 1:
            sink.diag(
                "multi-mount", "info",
                f"{router_var!r} mounted {len(entries)}x — first prefix used",
                path,
            )
        mount_str[router_var] = entries[0][1]
    return mount_str


def _emit_routes(
    *,
    sink: Emit,
    path: str,
    text: str,
    module: str,
    content_hash: str,
    services: dict[str, _ServiceInfo],
    includes: list[tuple[str, str, str]],
    routes: list[_Route],
    framework: str,
    confidence: ConfidenceClass,
    source: str,
) -> None:
    mount_str = _mount_prefixes(
        sink=sink, path=path, services=services, includes=includes,
    )

    svc_idents: dict[str, DomainIdentity] = {}

    def _service_ident(recv: str) -> DomainIdentity:
        cached = svc_idents.get(recv)
        if cached is not None:
            return cached
        info = services.get(recv)
        if info is None:
            info = _ServiceInfo("unknown", "", "", False, None)
            services[recv] = info
            sink.diag(
                "undeclared-receiver", "info",
                f"route receiver {recv!r} has no {framework} constructor "
                f"in {path}",
                path,
            )
        mount = mount_str.get(recv, "")
        qname = f"{module}#{recv}"
        ident = sink.entity(
            kind=DomainEntityKind.API_SERVICE,
            name=recv,
            qualified_name=qname,
            path=path,
            span=info.span,
            content_hash=content_hash,
            attributes={
                "framework": framework,
                "receiver": recv,
                "prefix": info.prefix,
                "mounted_prefix": mount,
                "source": source,
                "receiver_declared": "true" if info.declared else "false",
            },
            evidence=(
                sink.evi(path, content_hash, info.span, f"receiver {recv}"),
            ) if info.span else (),
        )
        svc_idents[recv] = ident
        sink.bump("api_services")
        return ident

    # CONTAINS app→router para mounts observados.
    for app_var, router_var, _mount in includes:
        app_ident = _service_ident(app_var)
        router_ident = _service_ident(router_var)
        span = services[router_var].span or services[app_var].span
        sink.relation(
            relation_type=EdgeKind.CONTAINS,
            path=path,
            from_identity=app_ident,
            to_identity=router_ident,
            resolution=sink.resolution(
                ResolutionStatus.RESOLVED, confidence,
                target=router_ident,
            ),
            span=span,
            evidence=(sink.evi(path, content_hash, span),) if span else (),
        )

    for route in routes:
        recv_ident = _service_ident(route.receiver)
        if route.method == "?" or not route.path:
            sink.diag(
                "dynamic-route-path", "warning",
                f"non-literal route path on {route.receiver!r} in {path} "
                f"— no endpoint identity emitted",
                path,
            )
            continue
        _emit_route_endpoint(
            sink=sink,
            path=path,
            module=module,
            content_hash=content_hash,
            route=route,
            recv_ident=recv_ident,
            full_prefix=common.join_paths(
                mount_str.get(route.receiver, ""),
                services[route.receiver].prefix,
            ),
            framework=framework,
            confidence=confidence,
            source=source,
        )


def _emit_route_endpoint(
    *,
    sink: Emit,
    path: str,
    module: str,
    content_hash: str,
    route: _Route,
    recv_ident: DomainIdentity,
    full_prefix: str,
    framework: str,
    confidence: ConfidenceClass,
    source: str,
) -> None:
    """APIEndpoint + CONTAINS (serviço→rota) + DEFINES (handler→rota)."""
    # mount vem antes do prefixo declarado: app.include_router(r, "/api")
    # + APIRouter(prefix="/v1") + @r.get("/x") → /api/v1/x.
    full_path = common.join_paths(full_prefix, route.path)
    ep = sink.endpoint(
        name=f"{route.method} {route.path}",
        qualified_name=f"{route.method} {full_path}",
        namespace=f"{module}#{route.receiver}",
        full_path=full_path,
        path=path,
        span=route.span,
        content_hash=content_hash,
        attributes={
            "method": route.method,
            "path": route.path,
            "full_path": full_path,
            "framework": framework,
            "handler": route.handler,
            "source": source,
        },
        evidence=(sink.evi(path, content_hash, route.span),)
        if route.span else (),
    )
    sink.bump("api_endpoints")
    sink.relation(
        relation_type=EdgeKind.CONTAINS,
        path=path,
        from_identity=recv_ident,
        to_identity=ep,
        resolution=sink.resolution(
            ResolutionStatus.RESOLVED, confidence, target=ep,
        ),
        span=route.span,
        evidence=(sink.evi(path, content_hash, route.span),)
        if route.span else (),
    )
    sink.relation(
        relation_type=EdgeKind.DEFINES,
        path=path,
        from_symbol=route.handler,
        to_identity=ep,
        resolution=sink.resolution(
            ResolutionStatus.RESOLVED, confidence, target=ep,
        ),
        span=route.span,
        evidence=(sink.evi(path, content_hash, route.span),)
        if route.span else (),
    )
