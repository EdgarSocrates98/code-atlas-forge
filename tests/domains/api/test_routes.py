"""Rotas FastAPI/Express em código → APIEndpoint + relações (lf-0114)."""

from __future__ import annotations

from code_atlas.contracts.enums import ConfidenceClass, ResolutionStatus
from code_atlas.domains.model import DomainEntityKind


def _by_kind(result, kind):
    return [e for e in result.entities if e.kind is kind]


def _qn(e):
    ident = getattr(e, "identity", e)
    return ident.qualified_name


def _diag_codes(result):
    return {d.code for d in result.diagnostics}


def _rels(result, rtype):
    return [r for r in result.relations if r.relation_type == rtype]


# -- FastAPI -------------------------------------------------------------


def test_fastapi_app_and_router_endpoints(extract) -> None:
    res = extract(["app/main.py", "app/routers.py"])
    endpoints = _by_kind(res, DomainEntityKind.API_ENDPOINT)
    assert {_qn(e) for e in endpoints} == {
        "GET /edge/health",
        "POST /v2/admin/users/{uid}",
        "GET /pets/",
        "GET /pets/{pet_id}",
    }
    by_qn = {_qn(e): e for e in endpoints}
    assert by_qn["GET /edge/health"].identity.namespace == "app.main#app"
    assert by_qn["GET /edge/health"].attributes["handler"] == "app.main.health"
    assert (
        by_qn["POST /v2/admin/users/{uid}"].identity.namespace
        == "app.main#admin"
    )
    assert by_qn["GET /pets/"].attributes["handler"] == "app.routers.list_pets"


def test_fastapi_services_and_prefixes(extract) -> None:
    res = extract(["app/main.py", "app/routers.py"])
    services = { _qn(s): s for s in _by_kind(res, DomainEntityKind.API_SERVICE) }
    assert services["app.main#app"].attributes["prefix"] == "/edge"
    assert services["app.main#app"].attributes["framework"] == "fastapi"
    assert services["app.main#admin"].attributes["prefix"] == "/admin"
    assert services["app.main#admin"].attributes["mounted_prefix"] == "/v2"
    assert services["app.main#admin"].attributes["receiver_declared"] == "true"
    # pets_router é montado em main.py mas declarado em routers.py —
    # serviço app.main#pets_router registra o mount observado.
    assert services["app.main#pets_router"].attributes["mounted_prefix"] == "/api"
    assert (
        services["app.main#pets_router"].attributes["receiver_declared"]
        == "false"
    )
    assert (
        services["app.routers#pets_router"].attributes["prefix"] == "/pets"
    )


def test_fastapi_relations_bridge_code_domain(extract) -> None:
    res = extract(["app/main.py", "app/routers.py"])
    defines = _rels(res, "DEFINES")
    contains = _rels(res, "CONTAINS")
    # handler symbol → endpoint (ponte código→domínio)
    by_from = {(r.from_symbol, _qn(r.to_identity)) for r in defines}
    assert ("app.main.health", "GET /edge/health") in by_from
    assert (
        "app.main.create_user", "POST /v2/admin/users/{uid}"
    ) in by_from
    assert ("app.routers.list_pets", "GET /pets/") in by_from
    # app CONTAINS router services (include_router observado)
    app_contains = {
        (_qn(r.from_identity), _qn(r.to_identity)) for r in contains
    }
    assert ("app.main#app", "app.main#admin") in app_contains
    assert ("app.main#app", "app.main#pets_router") in app_contains
    # serviço CONTAINS endpoint — resolvido dentro do resultado
    for r in contains:
        assert r.resolution.status is ResolutionStatus.RESOLVED
        assert r.resolution.confidence_class is ConfidenceClass.SYNTACTIC


def test_fastapi_dynamic_and_websocket_diagnostics(extract) -> None:
    res = extract(["app/main.py"])
    codes = _diag_codes(res)
    # f-string em decorator → marcador honesto, nenhum endpoint inventado
    assert "dynamic-route-path" in codes
    # websocket não é endpoint HTTP
    assert "non-http-decorator" in codes
    # router importado → limitação declarada
    assert "cross-file-router-prefix" in codes
    endpoints = _by_kind(res, DomainEntityKind.API_ENDPOINT)
    assert not any("dyn" in _qn(e) for e in endpoints)
    assert not any("/ws" in _qn(e) for e in endpoints)


def test_fastapi_parse_uses_real_ast_not_regex(extract) -> None:
    # O decorator é extraído do AST — um comentário mencionando
    # @app.get NÃO pode produzir endpoint.
    res = extract(["app/main.py", "app/routers.py"])
    endpoints = _by_kind(res, DomainEntityKind.API_ENDPOINT)
    assert all(
        not e.attributes["path"].startswith("#")
        for e in endpoints
    )
    assert res.relations  # sanity


# -- Express / TypeScript ----------------------------------------------------


def test_express_ts_routes_heuristic(extract) -> None:
    res = extract(["web/routes.ts"])
    endpoints = _by_kind(res, DomainEntityKind.API_ENDPOINT)
    assert {_qn(e) for e in endpoints} == {
        "GET /status", "POST /api/items", "GET /api/items/{id}",
    }
    by_qn = {_qn(e): e for e in endpoints}
    assert by_qn["GET /status"].identity.namespace == "web.routes#app"
    assert by_qn["POST /api/items"].identity.namespace == "web.routes#router"
    # heurística declarada: express = convenção de receiver
    contains = _rels(res, "CONTAINS")
    assert all(
        r.resolution.confidence_class is ConfidenceClass.HEURISTIC
        for r in contains
    )
    # mount observado: app.use("/api", router)
    assert ("web.routes#app", "web.routes#router") in {
        (_qn(r.from_identity), _qn(r.to_identity)) for r in contains
    }
    # template literal dinâmico → diagnóstico, não endpoint
    assert "dynamic-route-path" in _diag_codes(res)


def test_express_js_regex_static(extract) -> None:
    res = extract(["web/legacy.js"])
    endpoints = _by_kind(res, DomainEntityKind.API_ENDPOINT)
    assert {_qn(e) for e in endpoints} == {"GET /legacy"}
    ep = endpoints[0]
    assert ep.attributes["framework"] == "express"
    assert ep.attributes["source"] == "express-regex"
    contains = _rels(res, "CONTAINS")
    assert all(
        r.resolution.confidence_class is ConfidenceClass.HEURISTIC
        for r in contains
    )
