"""Bootstrap de resolvers opt-in a partir de ``config.toml`` (lf-0027).

Tabela suportada (tudo opcional; ausente = nenhum resolver externo):

.. code-block:: toml

    [resolvers.scip]
    index_path = "index.scip"          # relativo ao workspace ou absoluto
    max_bytes = 67108864               # override opcional
    allow_revision_mismatch = false    # opt-in explícito

    [resolvers.lsp.servers.pyright]
    command = ["pyright-langserver", "--stdio"]
    languages = ["python"]
    enabled = true
    request_timeout_s = 10.0

O bootstrap NUNCA executa nada do repositório indexado e NUNCA baixa
indexers/servers — ``command`` vem exclusivamente da config do usuário.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from code_atlas.resolvers.contracts import SemanticResolver
from code_atlas.resolvers.lsp.adapter import LspResolver
from code_atlas.resolvers.lsp.lifecycle import make_client
from code_atlas.resolvers.lsp.spawn import LspServerSpec
from code_atlas.resolvers.scip.loader import load_index
from code_atlas.resolvers.scip.provenance import assess_revision
from code_atlas.resolvers.scip.resolver import ScipResolver
from code_atlas.resolvers.syntactic.resolver import ScopeUniverse

ResolverFactory = Callable[[ScopeUniverse], SemanticResolver]


def resolver_factories(
    table: Mapping[str, object] | None,
    workspace_root: Path,
    versions_out: dict[str, str] | None = None,
) -> tuple[list[ResolverFactory], list[str]]:
    """Traduz a tabela ``[resolvers]`` em fábricas + erros tipados.

    Cada fábrica recebe o ``ScopeUniverse`` do build atual e devolve o
    resolver concreto; falha dentro da fábrica vira diagnóstico no
    enriquecimento (nunca crash).
    """
    factories: list[ResolverFactory] = []
    errors: list[str] = []
    if not table:
        return factories, errors
    scip = table.get("scip")
    if isinstance(scip, Mapping):
        _scip_factory(scip, workspace_root, factories, errors,
                      versions_out)
    lsp = table.get("lsp")
    if isinstance(lsp, Mapping):
        _lsp_factories(lsp, workspace_root, factories, errors)
    return factories, errors


def _scip_factory(
    scip: Mapping[str, object],
    root: Path,
    factories: list[ResolverFactory],
    errors: list[str],
    versions_out: dict[str, str] | None = None,
) -> None:
    index_path = scip.get("index_path")
    if not index_path or not isinstance(index_path, str):
        errors.append("resolvers.scip: index_path ausente ou inválido")
        return
    path = Path(index_path)
    if not path.is_absolute():
        path = root / path
    allow_mismatch = bool(scip.get("allow_revision_mismatch", False))
    max_bytes = scip.get("max_bytes")
    kwargs: dict[str, object] = {
        "workspace_root": root,
        "allow_revision_mismatch": allow_mismatch,
    }
    if isinstance(max_bytes, int):
        kwargs["max_bytes"] = max_bytes
    out = load_index(path, **kwargs)  # type: ignore[arg-type]
    if not out.ok or out.index is None:
        errors.append(
            f"scip index {path}: {'; '.join(out.errors) or 'load failed'}"
        )
        return
    index = out.index
    # Revision safety (lf-0028): índice válido ≠ índice compatível.
    # Rejected → erro e nenhuma factory; degraded → resolver marcado,
    # nunca promove como comprovado.
    assessment = assess_revision(index, path, root)
    prov = assessment.provenance
    if assessment.errors:
        errors.append(
            f"scip index {path}: {'; '.join(assessment.errors)}"
        )
        return
    if prov.validation_status == "degraded":
        errors.append(
            f"scip index {path}: degraded — "
            f"{'; '.join(prov.validation_evidence[:3])}"
        )
    if versions_out is not None:
        # sha256 do índice na identidade do index — índice SCIP novo
        # invalida relações (lf-0030 phase 6).
        versions_out["resolver:scip"] = prov.index_sha256[:16]
    factories.append(
        lambda universe: ScipResolver(index, universe, prov)
    )


def _lsp_factories(
    lsp: Mapping[str, object],
    root: Path,
    factories: list[ResolverFactory],
    errors: list[str],
) -> None:
    servers = lsp.get("servers")
    if not isinstance(servers, Mapping):
        return
    for name, spec in servers.items():
        if not isinstance(spec, Mapping):
            errors.append(f"resolvers.lsp.servers.{name}: spec inválido")
            continue
        cmd = spec.get("command")
        if not isinstance(cmd, (list, tuple)) or not cmd:
            errors.append(
                f"resolvers.lsp.servers.{name}: command ausente/inválido"
            )
            continue
        languages = spec.get("languages", ())
        server = LspServerSpec(
            name=str(name),
            cmd=tuple(str(c) for c in cmd),
            languages=frozenset(str(lang) for lang in languages)
            if isinstance(languages, (list, tuple))
            else frozenset(),
            root_uri=root.as_uri(),
            enabled=bool(spec.get("enabled", False)),
            request_timeout_s=float(spec.get("request_timeout_s", 10.0)),
        )
        if not server.enabled:
            continue
        def _factory(universe: ScopeUniverse,
                     _server: LspServerSpec = server) -> SemanticResolver:
            client = make_client(_server)
            if client is None:
                raise RuntimeError(f"lsp {_server.name}: disabled")
            return LspResolver(client, universe)

        factories.append(_factory)
