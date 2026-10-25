"""Contrato ``DomainAdapter`` do ``ApiContractAdapter`` (lf-0114)."""

from __future__ import annotations

import pytest

from code_atlas.contracts.enums import ConfidenceClass
from code_atlas.domains import (
    AdapterDisabledError,
    DiscoveredArtifact,
    DomainAdapter,
    DomainExtractionRequest,
    DomainRegistry,
    validate_extraction,
)
from code_atlas.domains.model import DOMAIN_RELATION_TYPES, DomainEntityKind

ALL_FIXTURES = [
    "app/client.py", "app/main.py", "app/routers.py",
    "openapi/anchored.yaml", "openapi/malformed.yaml",
    "openapi/not_a_spec.yaml", "openapi/petstore.yaml",
    "openapi/spec.json", "pkg/config.json",
    "web/client.ts", "web/legacy.js", "web/routes.ts",
]


def test_adapter_satisfies_protocol(adapter) -> None:
    assert isinstance(adapter, DomainAdapter)
    assert adapter.domain_id == "api"
    assert adapter.adapter_version


def test_declared_vocabulary_subset(adapter) -> None:
    assert adapter.entity_kinds() == frozenset(
        {
            DomainEntityKind.API_CONTRACT,
            DomainEntityKind.API_SERVICE,
            DomainEntityKind.API_ENDPOINT,
        }
    )
    assert adapter.relation_kinds() <= DOMAIN_RELATION_TYPES


def test_confidence_model_no_inferred(adapter) -> None:
    cm = adapter.confidence_model()
    assert cm.ceiling is ConfidenceClass.SYNTACTIC
    assert not cm.allows_inferred


def test_supported_artifacts(adapter) -> None:
    sup = adapter.supported_artifacts()
    for p in ("a/openapi.yaml", "b/s.yml", "c.json", "d.py", "e.ts",
              "f.tsx", "g.js"):
        assert sup.matches(p), p
    assert not sup.matches("readme.md")
    assert not sup.matches("x.tf")


def test_full_fixture_extract_validates(extract, adapter) -> None:
    res = extract(ALL_FIXTURES)
    req = DomainExtractionRequest(
        repository="git-remote:git@example/api-repo",
        artifacts=tuple(DiscoveredArtifact(path=p) for p in ALL_FIXTURES),
    )
    errors = validate_extraction(adapter, req, res)
    assert errors == []


def test_result_is_deterministic(extract) -> None:
    a = extract(ALL_FIXTURES)
    b = extract(ALL_FIXTURES)
    assert a.entities == b.entities
    assert a.relations == b.relations
    assert a.diagnostics == b.diagnostics
    assert a.evidence == b.evidence


def test_registry_registration_and_disable(adapter) -> None:
    reg = DomainRegistry()
    reg.register(adapter)
    assert reg.is_enabled("api")
    assert reg.require("api") is adapter
    reg.disable("api")
    assert not reg.is_enabled("api")
    with pytest.raises(AdapterDisabledError):
        reg.require("api")
    reg.enable("api")
    assert reg.require("api") is adapter


def test_unreadable_artifact_is_diagnostic_not_crash(adapter) -> None:
    res = adapter.extract(
        DomainExtractionRequest(
            repository="r",
            artifacts=(DiscoveredArtifact(path="ghost/missing.yaml"),),
        )
    )
    assert res.entities == ()
    assert any(d.code == "unreadable-artifact" for d in res.diagnostics)


def test_hash_mismatch_is_honest_warning(adapter) -> None:
    res = adapter.extract(
        DomainExtractionRequest(
            repository="r",
            artifacts=(
                DiscoveredArtifact(
                    path="openapi/petstore.yaml", sha256="0" * 64
                ),
            ),
        )
    )
    assert any(d.code == "artifact-hash-mismatch" for d in res.diagnostics)
    # extração segue com o hash real computado — nunca inventado
    assert res.entities


def test_no_external_network_or_urllib_in_sources() -> None:
    """Fronteira lf-0086: o pacote não pode usar urllib nem sockets."""
    import ast
    from pathlib import Path

    pkg = Path(__file__).resolve().parents[3] / "src" / "code_atlas" / "domains" / "api"
    offenders: list[str] = []
    banned = ("urllib", "socket", "requests", "httpx", "http.client")
    for py in pkg.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders += [
                    f"{py.name}:{node.lineno}"
                    for a in node.names
                    if a.name.split(".")[0] in banned
                ]
            elif isinstance(node, ast.ImportFrom) and (
                (node.module or "").split(".")[0] in banned
            ):
                offenders.append(f"{py.name}:{node.lineno}")
    assert offenders == []


def test_no_tree_sitter_imports_in_domain_api() -> None:
    """Fronteira parsers/: o adapter consome AST via ParseEngine."""
    import ast
    from pathlib import Path

    pkg = Path(__file__).resolve().parents[3] / "src" / "code_atlas" / "domains" / "api"
    for py in pkg.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(
                    a.name.startswith("tree_sitter") for a in node.names
                ), py
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("tree_sitter"), py


def test_stats_reported(extract) -> None:
    res = extract(ALL_FIXTURES)
    assert res.stats["openapi_specs"] == 4
    assert res.stats["api_endpoints"] == 15
    assert res.stats["api_services"] == 11
    assert res.stats["consumer_calls"] == 9
    assert res.stats["consumer_calls_dynamic"] == 2
    assert res.stats["schema_refs"] == 4
