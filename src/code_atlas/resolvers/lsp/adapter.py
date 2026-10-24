"""LspResolver — resolvedor EvidenceTier.LSP (lf-0018).

Envolve um ``LspClient`` ativo. Métodos LSP só são chamados quando o
servidor anunciou a capability no handshake — caso contrário, diagnóstico
``capability-unsupported`` e status honesto (nunca crash).

Mapeamento de alvos: ``Location{uri,range}`` → path relativo ao
``root_uri`` → nó de módulo do ``ScopeUniverse`` (identidade no nível de
arquivo, verificável). Precisão símbolo-a-símbolo é trabalho futuro;
quando o uri não pertence ao snapshot → ``external``/``unresolved``.

Proveniência: ``lsp:<server-name>:<version>`` — distinta de SCIP.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from code_atlas.resolvers.capabilities import EvidenceTier, ResolverCapability
from code_atlas.resolvers.contracts import (
    ResolutionDiagnostic,
    ResolutionRequest,
    ResolutionResult,
    ResolvedDefinition,
    ResolvedReference,
    ResolverProvenance,
)
from code_atlas.resolvers.lsp.lifecycle import LspClient, LspError
from code_atlas.resolvers.lsp.transport import TransportError
from code_atlas.resolvers.syntactic.resolver import ScopeUniverse

RESOLVER_ID_PREFIX = "lsp"
RESOLVER_VERSION = "1.0.0"

_KIND_TO_METHOD = {
    "definition": "textDocument/definition",
    "call": "textDocument/references",
    "reference": "textDocument/references",
    "type-ref": "textDocument/definition",
    "import": "textDocument/definition",
}


class LspResolver:
    """Implementa ``SemanticResolver`` sobre um LspClient ativo."""

    def __init__(self, client: LspClient, universe: ScopeUniverse) -> None:
        self._client = client
        self._u = universe

    @property
    def resolver_id(self) -> str:
        return f"{RESOLVER_ID_PREFIX}:{self._client.spec.name}"

    @property
    def resolver_version(self) -> str:
        return RESOLVER_VERSION

    def capabilities(self) -> ResolverCapability:
        return ResolverCapability(
            resolver_id=self.resolver_id,
            tier=EvidenceTier.LSP,
            languages=self._client.spec.languages,
            relation_types=frozenset(
                {"REFERENCES", "CALLS", "IMPORTS", "EXTENDS"}),
            needs_external_process=True,
            detail=f"stdio lsp server {self._client.spec.name}",
        )

    def _prov(self) -> ResolverProvenance:
        caps = self._client.capabilities
        detail = f"{caps.server_name}:{caps.server_version}".rstrip(":")
        return ResolverProvenance(
            resolver_id=self.resolver_id,
            resolver_version=RESOLVER_VERSION,
            evidence_tier=int(EvidenceTier.LSP),
            detail=detail,
        )

    # -- contrato ----------------------------------------------------------

    def resolve(self, request: ResolutionRequest) -> ResolutionResult:
        method = _KIND_TO_METHOD.get(request.kind)
        if method is None:
            return self._with_diag(request, "kind-unsupported",
                                   f"no LSP method for {request.kind}")
        if not self._client.available:
            return self._with_diag(request, "server-unavailable",
                                   "lsp server not running")
        if not self._client.capabilities.supports(method):
            return self._with_diag(request, "capability-unsupported",
                                   f"server did not advertise {method}")

        span = request.span
        if span is None:
            return self._with_diag(request, "span-required",
                                   "LSP requests need a source span")
        params: dict[str, Any] = {
            "textDocument": {"uri": self._doc_uri(request.path)},
            "position": {"line": span.start_line - 1,
                         "character": span.start_col},
        }
        if method == "textDocument/references":
            params["context"] = {"includeDeclaration": False}
        result = self._call(request, method, params)
        if isinstance(result, ResolutionResult):
            return result
        result_diags = tuple(
            ResolutionDiagnostic(
                code="server-diagnostic",
                severity="info",
                message=str(d)[:200],
                resolver_id=self.resolver_id,
            )
            for d in self._client.diagnostics_for(
                self._doc_uri(request.path))
        )
        mapped = self._map_result(request, self._locations(result))
        if result_diags:
            mapped = dataclasses.replace(
                mapped,
                diagnostics=result_diags + mapped.diagnostics,
            )
        return mapped

    def _call(
        self, request: ResolutionRequest, method: str,
        params: dict[str, Any],
    ) -> Any:
        """Request com recovery único embutido (lf-0029)."""
        try:
            return self._client.request(method, params)
        except (TimeoutError, LspError, TransportError) as exc:
            return self._recover_retry(request, method, params, exc)

    def _recover_retry(
        self, request: ResolutionRequest, method: str,
        params: dict[str, Any], exc: Exception,
    ) -> Any:
        """lf-0029: recovery pós-falha — UM restart + UM retry.
        Retorna o resultado do retry ou um ResolutionResult de erro
        tipado (fallback sintático segue valendo)."""
        if not self._try_restart():
            code = ("request-timeout" if isinstance(exc, TimeoutError)
                    else "server-error")
            return self._with_diag(
                request, code, f"{exc}; restart failed")
        try:
            return self._client.request(method, params)
        except (TimeoutError, LspError, TransportError) as exc2:
            return self._with_diag(
                request, "server-error", f"{exc}; after restart: {exc2}")

    def _try_restart(self) -> bool:
        """Uma tentativa de recovery — nunca lança."""
        try:
            self._client.restart()
        except Exception:
            return False
        return True

    # -- mapeamento de resultado -------------------------------------------

    def _map_result(
        self, request: ResolutionRequest, locations: list[dict[str, Any]]
    ) -> ResolutionResult:
        if not locations:
            return ResolutionResult(
                request=request,
                reference=ResolvedReference(
                    status="unresolved",
                    confidence_class="syntactic",
                    provenance=self._prov(),
                    target_text=request.name,
                ),
            )
        nids: list[str] = []
        foreign = False
        for loc in locations:
            path = self._rel_path(loc.get("uri", ""))
            if path is None:
                foreign = True
                continue
            nid = self._u.by_path.get(path)
            if nid and nid not in nids:
                nids.append(nid)
        if len(nids) == 1:
            status, conf = "resolved", "resolved"
        elif len(nids) > 1:
            status, conf = "ambiguous", "syntactic"
        else:
            status = "external" if foreign else "unresolved"
            conf = "syntactic"
        return ResolutionResult(
            request=request,
            reference=ResolvedReference(
                status=status,
                confidence_class=conf,
                provenance=self._prov(),
                target_id=nids[0] if len(nids) == 1 else None,
                target_text=request.name,
                candidates=tuple(sorted(nids)) if len(nids) > 1 else (),
            ),
            definitions=tuple(
                ResolvedDefinition(
                    symbol=request.name,
                    path=self._rel_path(loc.get("uri", "")) or "",
                    provenance=self._prov(),
                )
                for loc in locations
            ),
        )

    # -- helpers -----------------------------------------------------------

    def _doc_uri(self, path: str) -> str:
        root = self._client.spec.root_uri.rstrip("/")
        return f"{root}/{path}"

    def _rel_path(self, uri: str) -> str | None:
        """file:// URI → path relativo ao root_uri; None se fora do repo."""
        root = self._client.spec.root_uri.rstrip("/") + "/"
        if uri.startswith(root):
            return uri[len(root):]
        return None

    @staticmethod
    def _locations(result: object) -> list[dict[str, Any]]:
        """definition/references → lista de Location (obj ou lista)."""
        if result is None:
            return []
        if isinstance(result, dict):  # Location único ou LocationLink
            if "targetUri" in result:
                return [{"uri": result["targetUri"],
                         "range": result.get("targetSelectionRange", {})}]
            return [result] if "uri" in result else []
        if isinstance(result, list):
            return [r for r in result if isinstance(r, dict)]
        return []

    def _with_diag(
        self, request: ResolutionRequest, code: str, message: str
    ) -> ResolutionResult:
        return ResolutionResult(
            request=request,
            reference=ResolvedReference(
                status="unresolved",
                confidence_class="syntactic",
                provenance=self._prov(),
                target_text=request.name,
            ),
            diagnostics=(ResolutionDiagnostic(
                code=code, severity="warning", message=message,
                resolver_id=self.resolver_id),),
        )
