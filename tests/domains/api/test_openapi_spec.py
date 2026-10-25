"""Extração OpenAPI/Swagger → APIContract/APIService/APIEndpoint (lf-0114)."""

from __future__ import annotations

from code_atlas.contracts.enums import ResolutionStatus
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


def test_petstore_contract_service_endpoints(extract) -> None:
    res = extract(["openapi/petstore.yaml"])
    contracts = _by_kind(res, DomainEntityKind.API_CONTRACT)
    services = _by_kind(res, DomainEntityKind.API_SERVICE)
    endpoints = _by_kind(res, DomainEntityKind.API_ENDPOINT)
    assert len(contracts) == 1
    assert _qn(contracts[0]) == "openapi/petstore.yaml"
    assert contracts[0].attributes["format"] == "yaml"
    assert contracts[0].attributes["api_version"] == "3.0.0"

    assert len(services) == 1
    svc = services[0]
    assert _qn(svc) == "openapi/petstore.yaml#pet-store"
    assert svc.attributes["base_path"] == "/v1"
    assert svc.attributes["server_url"] == "https://api.pets.example/v1"
    assert svc.attributes["source"] == "openapi-spec"

    assert {_qn(e) for e in endpoints} == {
        "GET /v1/pets", "POST /v1/pets", "GET /v1/pets/{petId}",
    }
    for e in endpoints:
        assert e.identity.namespace == "openapi/petstore.yaml#pet-store"
        assert e.attributes["spec"] == "openapi/petstore.yaml"
        assert e.attributes["base_path"] == "/v1"
        assert e.attributes["source"] == "openapi-spec"
    by_qn = {_qn(e): e for e in endpoints}
    assert by_qn["GET /v1/pets"].attributes["operation_id"] == "listPets"
    assert by_qn["GET /v1/pets"].attributes["tags"] == "pets|listing"
    # spans reais apontam para as linhas `get:`/`post:` do spec
    assert by_qn["GET /v1/pets"].span is not None
    assert by_qn["GET /v1/pets"].span.start_line == 11


def test_petstore_relations(extract) -> None:
    res = extract(["openapi/petstore.yaml"])
    defines = _rels(res, "DEFINES")
    contains = _rels(res, "CONTAINS")
    refs = _rels(res, "REFERENCES")

    assert len(defines) == 1
    d = defines[0]
    assert _qn(d.from_identity) == "openapi/petstore.yaml"
    assert _qn(d.to_identity) == "openapi/petstore.yaml#pet-store"
    assert d.resolution.status is ResolutionStatus.RESOLVED

    assert len(contains) == 3
    for c in contains:
        assert _qn(c.from_identity) == "openapi/petstore.yaml#pet-store"
        assert c.resolution.status is ResolutionStatus.RESOLVED

    # schema $refs: response de listPets, request+response de createPet/showPet
    assert len(refs) == 3
    targets = sorted(r.to_unresolved for r in refs)
    assert targets == [
        "#/components/schemas/Pet",
        "#/components/schemas/Pet",
        "#/components/schemas/Pets",
    ]
    for r in refs:
        assert r.resolution.status is ResolutionStatus.UNRESOLVED


def test_json_spec_extracted(extract) -> None:
    res = extract(["openapi/spec.json"])
    endpoints = _by_kind(res, DomainEntityKind.API_ENDPOINT)
    assert {_qn(e) for e in endpoints} == {"GET /base/things"}
    svc = _by_kind(res, DomainEntityKind.API_SERVICE)[0]
    assert svc.attributes["base_path"] == "/base"
    refs = _rels(res, "REFERENCES")
    assert [r.to_unresolved for r in refs] == ["#/components/schemas/Thing"]


def test_anchors_produce_honest_diagnostics_and_partial(extract) -> None:
    res = extract(["openapi/anchored.yaml"])
    codes = _diag_codes(res)
    assert "yaml-anchor-unsupported" in codes
    assert "yaml-merge-key-unsupported" in codes
    assert "yaml-alias-unsupported" in codes
    # extração parcial honesta: endpoint ainda emerge
    endpoints = _by_kind(res, DomainEntityKind.API_ENDPOINT)
    assert {_qn(e) for e in endpoints} == {"GET /api/x"}


def test_malformed_spec_partial_with_error_diagnostics(extract) -> None:
    res = extract(["openapi/malformed.yaml"])
    codes = _diag_codes(res)
    assert "flow-parse-error" in codes
    assert "not-a-mapping" in codes
    # parcial: /ok extraído, /bad também (op vazio → endpoint sem operation_id)
    endpoints = _by_kind(res, DomainEntityKind.API_ENDPOINT)
    assert {_qn(e) for e in endpoints} == {"GET /v9/ok", "GET /v9/bad"}
    by_qn = {_qn(e): e for e in endpoints}
    assert by_qn["GET /v9/ok"].attributes["operation_id"] == "getOk"
    assert "operation_id" not in by_qn["GET /v9/bad"].attributes


def test_non_spec_yaml_skipped_with_info(extract) -> None:
    res = extract(["openapi/not_a_spec.yaml"])
    assert res.entities == ()
    assert "not-openapi-artifact" in _diag_codes(res)


def test_non_spec_json_skipped_with_info(extract) -> None:
    res = extract(["pkg/config.json"])
    assert res.entities == ()
    assert "not-openapi-artifact" in _diag_codes(res)


def test_identity_carries_repository(extract) -> None:
    res = extract(["openapi/petstore.yaml"], repository="other-repo")
    for e in res.entities:
        assert e.identity.repository == "other-repo"
        assert e.identity.domain == "api"
