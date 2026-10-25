"""Registro de adaptadores de domínio (lf-0110, Cycle 7).

**Registro explícito** (mesmo desenho de ``ResolverRegistry``, ADR-0010):
nenhum adapter é ativado por padrão, nenhum código de terceiros é
carregado por varredura automática. ``discover_entrypoints`` existe como
mecanismo **opt-in** — só roda quando o caller invoca explicitamente.

Semântica honesta de ausência: domínio não registrado NÃO é erro de
extração — adapters são opcionais (spec). ``get`` devolve ``None``;
``require`` levanta exceção tipada para quem precisa do domínio.
Adapters desabilitados produzem **nada**: ``enabled()`` os exclui e
``require`` recusa.
"""

from __future__ import annotations

import importlib.metadata
import re
from collections.abc import Callable

from code_atlas.domains.contracts import DomainAdapter

ENTRYPOINT_GROUP = "code_atlas.domain_adapters"


class AdapterRegistryError(Exception):
    """Falha de registro/lookup de adapter de domínio."""


class UnknownDomainError(AdapterRegistryError):
    """Domínio requerido não está registrado."""


class AdapterDisabledError(AdapterRegistryError):
    """Domínio registrado mas desabilitado — não produz extração."""


class DuplicateAdapterError(AdapterRegistryError):
    """Par (domain_id, adapter_version) já registrado."""


def _version_key(version: str) -> tuple[tuple[int, ...], str]:
    """Chave de ordenação semver-ish: segmentos numéricos + tiebreak lexical."""
    nums = tuple(int(p) for p in re.findall(r"\d+", version))
    return (nums, version)


def _check_adapter(adapter: DomainAdapter) -> None:
    """Validação mínima do contrato no momento do registro."""
    if not isinstance(adapter, DomainAdapter):
        raise AdapterRegistryError(
            f"object does not satisfy DomainAdapter: {adapter!r}"
        )
    if not adapter.domain_id.strip():
        raise AdapterRegistryError("adapter requires non-empty domain_id")
    if not adapter.adapter_version.strip():
        raise AdapterRegistryError(
            f"adapter {adapter.domain_id!r} requires non-empty version"
        )
    adapter.entity_kinds()      # materializa — valida early
    adapter.relation_kinds()
    adapter.confidence_model()


class DomainRegistry:
    """Registro explícito de adapters de domínio com versionamento.

    ``register`` nunca substitui silenciosamente: mesmo ``domain_id`` com
    versões distintas convivem (rastreabilidade de upgrade); mesma versão
    registrada duas vezes é ``DuplicateAdapterError``.
    """

    def __init__(self) -> None:
        self._adapters: dict[str, dict[str, DomainAdapter]] = {}
        self._disabled: set[str] = set()
        self._entrypoint_errors: list[str] = []

    # --- registro ----------------------------------------------------

    def register(self, adapter: DomainAdapter, *, enabled: bool = True) -> None:
        """Registra ``adapter``; ``enabled=False`` registra desabilitado."""
        _check_adapter(adapter)
        versions = self._adapters.setdefault(adapter.domain_id, {})
        if adapter.adapter_version in versions:
            raise DuplicateAdapterError(
                f"adapter already registered: "
                f"{adapter.domain_id}@{adapter.adapter_version}"
            )
        versions[adapter.adapter_version] = adapter
        if not enabled:
            self._disabled.add(adapter.domain_id)

    def unregister(self, domain_id: str, version: str | None = None) -> bool:
        """Remove ``version`` (ou todas quando ``None``). True se removeu."""
        versions = self._adapters.get(domain_id)
        if not versions:
            return False
        if version is None:
            del self._adapters[domain_id]
            self._disabled.discard(domain_id)
            return True
        if version not in versions:
            return False
        del versions[version]
        if not versions:
            del self._adapters[domain_id]
            self._disabled.discard(domain_id)
        return True

    def discover_entrypoints(
        self, group: str = ENTRYPOINT_GROUP
    ) -> int:
        """Carrega adapters declarados via ``entry_points`` instalados.

        **Opt-in explícito** — nunca chamado automaticamente (ADR-0010):
        importar código de terceiros é decisão do caller, não do
        registry. Cada entry point deve resolver para um
        ``DomainAdapter`` ou uma fábrica ``() -> DomainAdapter``.
        Falhas são registradas em ``entrypoint_errors`` e não abortam
        os demais. Retorna quantos adapters foram registrados.
        """
        loaded = 0
        for ep in importlib.metadata.entry_points(group=group):
            try:
                target = ep.load()
                adapter: DomainAdapter = (
                    target() if callable(target) else target
                )
                self.register(adapter)
            except Exception as exc:  # diagnóstico honesto, não crash
                self._entrypoint_errors.append(f"{ep.name}: {exc}")
                continue
            loaded += 1
        return loaded

    @property
    def entrypoint_errors(self) -> tuple[str, ...]:
        return tuple(self._entrypoint_errors)

    # --- enable / disable --------------------------------------------

    def enable(self, domain_id: str) -> None:
        if domain_id not in self._adapters:
            raise UnknownDomainError(f"domain not registered: {domain_id!r}")
        self._disabled.discard(domain_id)

    def disable(self, domain_id: str) -> None:
        if domain_id not in self._adapters:
            raise UnknownDomainError(f"domain not registered: {domain_id!r}")
        self._disabled.add(domain_id)

    def is_enabled(self, domain_id: str) -> bool:
        return (
            domain_id in self._adapters and domain_id not in self._disabled
        )

    # --- lookup --------------------------------------------------------

    def _pick(self, domain_id: str, version: str | None) -> DomainAdapter | None:
        versions = self._adapters.get(domain_id)
        if not versions:
            return None
        if version is not None:
            return versions.get(version)
        if len(versions) == 1:
            return next(iter(versions.values()))
        # versão mais alta por ordenação semver-ish — determinístico
        return versions[max(versions, key=_version_key)]

    def get(
        self, domain_id: str, version: str | None = None
    ) -> DomainAdapter | None:
        """Adapter registrado (habilitado ou não); ``None`` se ausente.

        ``version=None`` retorna a maior versão registrada.
        """
        return self._pick(domain_id, version)

    def require(
        self, domain_id: str, version: str | None = None
    ) -> DomainAdapter:
        """Adapter **habilitado** ou exceção tipada e honesta.

        - não registrado → ``UnknownDomainError``;
        - registrado mas desabilitado → ``AdapterDisabledError``;
        - versão inexistente → ``UnknownDomainError`` com detalhe.
        """
        versions = self._adapters.get(domain_id)
        if not versions:
            raise UnknownDomainError(f"domain not registered: {domain_id!r}")
        if domain_id in self._disabled:
            raise AdapterDisabledError(f"domain disabled: {domain_id!r}")
        adapter = self._pick(domain_id, version)
        if adapter is None:
            raise UnknownDomainError(
                f"version not registered: {domain_id!r}@{version!r}"
            )
        return adapter

    # --- inventário ------------------------------------------------------

    def versions(self, domain_id: str) -> tuple[str, ...]:
        """Versões registradas do domínio, ordenadas."""
        versions = self._adapters.get(domain_id, {})
        return tuple(sorted(versions, key=_version_key))

    def domains(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))

    def adapters(self) -> tuple[DomainAdapter, ...]:
        """Todos os adapters registrados (habilitados e desabilitados)."""
        return tuple(
            a for versions in self._adapters.values() for a in versions.values()
        )

    def enabled(self) -> tuple[DomainAdapter, ...]:
        """Adapters habilitados — maior versão por domínio.

        É o conjunto de extração: domínio desabilitado ou não registrado
        produz exatamente zero entidades/relações.
        """
        out: list[DomainAdapter] = []
        for domain_id in sorted(self._adapters):
            if domain_id in self._disabled:
                continue
            picked = self._pick(domain_id, None)
            if picked is not None:
                out.append(picked)
        return tuple(out)

    def discovery_candidates(self, paths: list[str]) -> dict[str, list[str]]:
        """Mapa ``domain_id → paths`` suportados pelos adapters habilitados.

        Filtro puro por ``ArtifactSupport`` — não executa adapters.
        """
        out: dict[str, list[str]] = {}
        for adapter in self.enabled():
            support = adapter.supported_artifacts()
            out[adapter.domain_id] = [p for p in paths if support.matches(p)]
        return out


def default_registry(
    factories: tuple[Callable[[], DomainAdapter], ...] = (),
) -> DomainRegistry:
    """Constrói um registry populado por fábricas explícitas do caller."""
    reg = DomainRegistry()
    for f in factories:
        reg.register(f())
    return reg
