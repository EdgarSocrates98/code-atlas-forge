"""DomainRegistry: registro explícito, versionamento, enable/disable.

Casos adversariais da spec: adapter desabilitado não produz nada;
domínio não registrado é recusado honestamente; adapters opcionais
nunca são obrigatórios.
"""

from __future__ import annotations

import pytest

from code_atlas.contracts.enums import ConfidenceClass
from code_atlas.domains import (
    AdapterDisabledError,
    AdapterRegistryError,
    ArtifactSupport,
    ConfidenceModel,
    DiscoveryRule,
    DomainAdapter,
    DomainEntityKind,
    DomainExtractionRequest,
    DomainExtractionResult,
    DomainRegistry,
    DuplicateAdapterError,
    UnknownDomainError,
)


class _FakeAdapter:
    """Satisfaz o protocolo DomainAdapter sem ser um declarado."""

    def __init__(
        self,
        domain_id: str = "sql",
        version: str = "0.1.0",
        extensions: frozenset[str] = frozenset({".sql"}),
    ) -> None:
        self._id = domain_id
        self._version = version
        self._support = ArtifactSupport(extensions=extensions)

    @property
    def domain_id(self) -> str:
        return self._id

    @property
    def adapter_version(self) -> str:
        return self._version

    def supported_artifacts(self) -> ArtifactSupport:
        return self._support

    def entity_kinds(self) -> frozenset[DomainEntityKind]:
        return frozenset({DomainEntityKind.DATA_TABLE})

    def relation_kinds(self) -> frozenset[str]:
        return frozenset({"READS_FROM"})

    def confidence_model(self) -> ConfidenceModel:
        return ConfidenceModel(ceiling=ConfidenceClass.SYNTACTIC)

    def discovery_rules(self) -> tuple[DiscoveryRule, ...]:
        return (DiscoveryRule(glob="*.sql", artifact_kind="sql-script"),)

    def extract(
        self, request: DomainExtractionRequest
    ) -> DomainExtractionResult:
        return DomainExtractionResult(
            domain_id=self._id, adapter_version=self._version
        )


def test_protocol_runtime_check() -> None:
    assert isinstance(_FakeAdapter(), DomainAdapter)
    assert not isinstance(object(), DomainAdapter)


def test_register_and_get() -> None:
    reg = DomainRegistry()
    adapter = _FakeAdapter()
    reg.register(adapter)
    assert reg.get("sql") is adapter
    assert reg.get("sql", "0.1.0") is adapter
    assert reg.is_enabled("sql")
    assert reg.domains() == ("sql",)
    assert reg.versions("sql") == ("0.1.0",)


def test_unregistered_domain_returns_none_not_error() -> None:
    """Adapters opcionais nunca são obrigatórios — ausência é honesta."""
    reg = DomainRegistry()
    assert reg.get("terraform") is None
    assert reg.enabled() == ()
    with pytest.raises(UnknownDomainError, match="not registered"):
        reg.require("terraform")


def test_duplicate_domain_version_rejected() -> None:
    reg = DomainRegistry()
    reg.register(_FakeAdapter(version="0.1.0"))
    with pytest.raises(DuplicateAdapterError, match=r"0\.1\.0"):
        reg.register(_FakeAdapter(version="0.1.0"))


def test_multiple_versions_coexist_latest_wins() -> None:
    reg = DomainRegistry()
    reg.register(_FakeAdapter(version="0.2.0"))
    reg.register(_FakeAdapter(version="0.10.0"))
    # ordenação semver numérica: 0.10.0 > 0.2.0 (lexical falharia)
    assert reg.get("sql").adapter_version == "0.10.0"  # type: ignore[union-attr]
    assert reg.versions("sql") == ("0.2.0", "0.10.0")
    assert reg.get("sql", "0.2.0").adapter_version == "0.2.0"  # type: ignore[union-attr]


def test_require_unknown_version_raises() -> None:
    reg = DomainRegistry()
    reg.register(_FakeAdapter(version="0.1.0"))
    with pytest.raises(UnknownDomainError, match="version not registered"):
        reg.require("sql", "9.9.9")


def test_disabled_adapter_produces_nothing() -> None:
    """Caso adversarial: desabilitar deve zerar a produção do domínio."""
    reg = DomainRegistry()
    reg.register(_FakeAdapter())
    reg.disable("sql")
    assert not reg.is_enabled("sql")
    assert reg.enabled() == ()
    assert reg.discovery_candidates(["a.sql", "b.tf"]) == {}
    with pytest.raises(AdapterDisabledError, match="disabled"):
        reg.require("sql")
    reg.enable("sql")
    assert reg.is_enabled("sql")
    assert len(reg.enabled()) == 1


def test_register_disabled_then_enable() -> None:
    reg = DomainRegistry()
    reg.register(_FakeAdapter(), enabled=False)
    assert not reg.is_enabled("sql")
    assert reg.enabled() == ()
    reg.enable("sql")
    assert reg.is_enabled("sql")


def test_enable_unknown_domain_raises() -> None:
    reg = DomainRegistry()
    with pytest.raises(UnknownDomainError):
        reg.enable("nope")
    with pytest.raises(UnknownDomainError):
        reg.disable("nope")


def test_register_non_adapter_rejected() -> None:
    reg = DomainRegistry()
    with pytest.raises(AdapterRegistryError, match="DomainAdapter"):
        reg.register(object())  # type: ignore[arg-type]


def test_unregister() -> None:
    reg = DomainRegistry()
    reg.register(_FakeAdapter(version="0.1.0"))
    reg.register(_FakeAdapter(version="0.2.0"))
    assert reg.unregister("sql", "0.1.0")
    assert reg.versions("sql") == ("0.2.0",)
    assert reg.unregister("sql")  # remove o resto
    assert reg.get("sql") is None
    assert not reg.unregister("sql")


def test_discovery_candidates_filters_by_support() -> None:
    reg = DomainRegistry()
    reg.register(_FakeAdapter())
    out = reg.discovery_candidates(["a.sql", "b.tf", "c.sql"])
    assert out == {"sql": ["a.sql", "c.sql"]}


class _FakeEntryPoint:
    def __init__(self, name: str, target: object, fail: bool = False) -> None:
        self.name = name
        self._target = target
        self._fail = fail

    def load(self) -> object:
        if self._fail:
            raise RuntimeError("boom")
        return self._target


def test_entrypoints_never_auto_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discovery só acontece quando o caller pede — nunca no register."""
    import importlib.metadata

    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda group: [_FakeEntryPoint("sql", _FakeAdapter)],
    )
    reg = DomainRegistry()
    assert reg.adapters() == ()  # nada carregado automaticamente
    loaded = reg.discover_entrypoints()
    assert loaded == 1
    assert reg.get("sql") is not None


def test_entrypoint_errors_recorded_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib.metadata

    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda group: [
            _FakeEntryPoint("bad", None, fail=True),
            _FakeEntryPoint("ok", _FakeAdapter),
        ],
    )
    reg = DomainRegistry()
    assert reg.discover_entrypoints() == 1
    assert len(reg.entrypoint_errors) == 1
    assert "bad" in reg.entrypoint_errors[0]
