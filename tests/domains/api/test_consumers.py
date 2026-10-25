"""Consumidores HTTP literais → CALLS_ENDPOINT honesto (lf-0114).

Regra: só literais estáticos viram URL; dinâmicos viram marcador
``dynamic:`` nunca resolvido; o vínculo URL↔endpoint é pista
(``candidates``) — resolução é Phase 9, nunca forçada aqui.
"""

from __future__ import annotations

from code_atlas.contracts.enums import ResolutionStatus


def _calls(result):
    return [r for r in result.relations
            if r.relation_type == "CALLS_ENDPOINT"]


def _diag_codes(result):
    return {d.code for d in result.diagnostics}


def test_python_consumers_literal_calls(extract) -> None:
    res = extract(["app/client.py"])
    calls = _calls(res)
    by_target = {r.to_unresolved: r for r in calls}
    assert "https://api.pets.example/v1/pets" in by_target
    assert "/v1/pets" in by_target
    assert "https://api.pets.example/v1/pets/1" in by_target
    assert "https://other.example/ping" in by_target

    abs_call = by_target["https://api.pets.example/v1/pets"]
    assert abs_call.from_symbol == "app.client.fetch_pets"
    assert abs_call.resolution.status is ResolutionStatus.EXTERNAL

    rel_call = by_target["/v1/pets"]
    assert rel_call.from_symbol == "app.client.fetch_pets"
    assert rel_call.resolution.status is ResolutionStatus.UNRESOLVED

    # requests.request("DELETE", url): método literal registrado
    req = by_target["https://api.pets.example/v1/pets/1"]
    assert req.from_symbol == "app.client"
    assert req.resolution.status is ResolutionStatus.EXTERNAL
    evi = res.evidence[req.evidence[0]]
    assert "method=DELETE" in evi.notes


def test_python_dynamic_url_never_resolved(extract) -> None:
    res = extract(["app/client.py"])
    calls = _calls(res)
    dyn = [r for r in calls if r.to_unresolved.startswith("dynamic:")]
    assert len(dyn) == 1
    assert dyn[0].from_symbol == "app.client.fetch_dynamic"
    assert dyn[0].resolution.status is ResolutionStatus.UNRESOLVED
    assert "dynamic-url" in _diag_codes(res)


def test_ts_consumers_fetch_and_axios(extract) -> None:
    res = extract(["web/client.ts"])
    calls = _calls(res)
    abs_calls = [
        r for r in calls
        if r.to_unresolved == "https://api.pets.example/v1/pets"
    ]
    # fetch() dentro de loadPets + axios.post() no nível do módulo
    assert {r.from_symbol for r in abs_calls} == {
        "web.client.loadPets", "web.client",
    }
    assert all(
        r.resolution.status is ResolutionStatus.EXTERNAL
        for r in abs_calls
    )
    by_target = {r.to_unresolved: r for r in calls
                 if not r.to_unresolved.startswith("https://")}
    rel = by_target["/v1/pets/1"]
    assert rel.resolution.status is ResolutionStatus.UNRESOLVED
    # axios.request({url: "/v1/pets/2"}) — url literal dentro do config
    assert "/v1/pets/2" in by_target
    assert by_target["/v1/pets/2"].from_symbol == "web.client"
    # template literal → marcador dinâmico
    dyn = [r for r in calls if r.to_unresolved.startswith("dynamic:")]
    assert len(dyn) == 1
    assert "${cfg.base}/dyn" in dyn[0].to_unresolved


def test_js_consumer_regex_static(extract) -> None:
    res = extract(["web/legacy.js"])
    calls = _calls(res)
    assert len(calls) == 1
    assert calls[0].to_unresolved == "/v1/pets"
    assert calls[0].from_symbol == "web.legacy"
    assert calls[0].resolution.status is ResolutionStatus.UNRESOLVED


def test_candidates_hint_not_resolution(extract) -> None:
    """Literal que casa path de endpoint local → candidates, nunca resolved."""
    res = extract(["openapi/petstore.yaml", "app/client.py"])
    calls = _calls(res)
    pet_call = next(
        r for r in calls if r.to_unresolved == "/v1/pets"
    )
    assert pet_call.resolution.status is ResolutionStatus.UNRESOLVED
    cand_qn = {c.qualified_name for c in pet_call.resolution.candidates}
    # GET e POST compartilham /v1/pets — a desambiguação é Phase 9.
    assert cand_qn == {"GET /v1/pets", "POST /v1/pets"}
    # mesmo para URL absoluta: external + candidates
    abs_call = next(
        r for r in calls
        if r.to_unresolved == "https://api.pets.example/v1/pets"
    )
    assert abs_call.resolution.status is ResolutionStatus.EXTERNAL
    assert {c.qualified_name for c in abs_call.resolution.candidates} == {
        "GET /v1/pets", "POST /v1/pets",
    }


def test_candidates_independent_of_artifact_order(extract) -> None:
    """Consumidor antes do spec na ordem alfabética ainda vê candidatos."""
    res = extract(["app/client.py", "openapi/petstore.yaml"])
    calls = _calls(res)
    pet_call = next(r for r in calls if r.to_unresolved == "/v1/pets")
    assert len(pet_call.resolution.candidates) == 2


def test_call_without_matching_endpoint_has_no_candidates(extract) -> None:
    res = extract(["openapi/petstore.yaml", "app/client.py"])
    calls = _calls(res)
    ping = next(
        r for r in calls
        if r.to_unresolved == "https://other.example/ping"
    )
    assert ping.resolution.candidates == ()
