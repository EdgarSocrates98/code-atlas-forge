"""``ApiContractAdapter`` — adapter de domínio para APIs/contratos (lf-0114).

Phase 6 do Cycle 7. Fontes observadas (somente leitura, nunca execução):

- **OpenAPI 3.x / Swagger 2.0** (``.yaml``/``.yml``/``.json``): parser
  YAML de subconjunto delimitado local (``yaml_subset`` — PyYAML não é
  dependência aprovada) + ``json`` stdlib → ``APIContract`` +
  ``APIService`` + ``APIEndpoint`` (identidade ``METHOD base_path+path``)
  + ``REFERENCES`` para ``$ref`` de request/response.
- **FastAPI** (``.py``): decorators ``@app.get``/``@router.*`` extraídos
  de artefatos AST reais via ``ParseEngine`` (Tree-sitter atrás da
  fronteira ``parsers/``) → ``APIService``/``APIEndpoint`` +
  ``CONTAINS``/``DEFINES`` (ponte handler→endpoint) — ``syntactic``.
- **Express** (``.ts``/``.tsx`` via AST; ``.js``/``.mjs``/``.cjs`` via
  regex-static por linha): literais ``app.get``/``router.post`` → mesmo
  esquema, confiança ``heuristic`` declarada.
- **Consumidores**: ``requests.*``/``httpx.*``/``urlopen``/``fetch``/
  ``axios.*`` com URL literal → ``CALLS_ENDPOINT`` ``unresolved``/
  ``external`` + ``candidates`` (a resolução literal↔contrato é Phase 9);
  URLs dinâmicas → marcador ``dynamic:`` nunca resolvido.
- **API Gateway em IaC**: fora de escopo — só ``REFERENCES`` triviais
  quando visíveis diretamente (não implementado neste slice).

Leitura de artefatos: o contrato ``DomainAdapter`` proíbe ler disco fora
do request — o adapter lê ``workspace_root / artifact.path`` apenas para
artefatos listados em ``request.artifacts`` (o coordinator escolhe o
recorte; ``workspace_root`` é injeção do caller, default ``.``).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from code_atlas.contracts.enums import ConfidenceClass, EdgeKind
from code_atlas.discovery.scanner import (
    artifact_path_confined,
    read_file_verified,
)
from code_atlas.domains.api import common, consumers, openapi, routes
from code_atlas.domains.api.emit import ADAPTER_DOMAIN, Emit
from code_atlas.domains.api.yaml_subset import parse_yaml_subset
from code_atlas.domains.contracts import (
    ArtifactSupport,
    ConfidenceModel,
    DiscoveryRule,
    DomainAdapter,
    DomainExtractionRequest,
    DomainExtractionResult,
)
from code_atlas.domains.model import DomainEntityKind, DomainRelationKind
from code_atlas.parsers.contracts import ParseResult
from code_atlas.parsers.engine import ParseEngine

ADAPTER_VERSION = "0.1.0"

_SPEC_EXTS = frozenset({".yaml", ".yml", ".json"})
_PY_EXTS = frozenset({".py", ".pyi"})
_TS_EXTS = frozenset({".ts", ".tsx"})
_JS_EXTS = frozenset({".js", ".jsx", ".mjs", ".cjs"})
_MAX_ARTIFACT = 4 << 20  # 4 MiB — guarda contra artefatos hostis


class ApiContractAdapter:
    """Adapter ``api``: contratos OpenAPI + rotas/consumidores em código."""

    def __init__(
        self,
        workspace_root: str | Path = ".",
        *,
        engine: ParseEngine | None = None,
    ) -> None:
        self._root = Path(workspace_root)
        self._engine = engine or ParseEngine()

    # -- contrato ------------------------------------------------------

    @property
    def domain_id(self) -> str:
        return ADAPTER_DOMAIN

    @property
    def adapter_version(self) -> str:
        return ADAPTER_VERSION

    def supported_artifacts(self) -> ArtifactSupport:
        return ArtifactSupport(
            extensions=_SPEC_EXTS | _PY_EXTS | _TS_EXTS | _JS_EXTS,
        )

    def entity_kinds(self) -> frozenset[DomainEntityKind]:
        return frozenset(
            {
                DomainEntityKind.API_CONTRACT,
                DomainEntityKind.API_SERVICE,
                DomainEntityKind.API_ENDPOINT,
            }
        )

    def relation_kinds(self) -> frozenset[str]:
        return frozenset(
            {
                str(EdgeKind.CONTAINS),
                str(EdgeKind.DEFINES),
                str(DomainRelationKind.CALLS_ENDPOINT),
                str(EdgeKind.REFERENCES),
            }
        )

    def confidence_model(self) -> ConfidenceModel:
        # Extração local evidence-first: teto syntactic; Express/.js
        # declaram heuristic; nada inferred é emitido.
        return ConfidenceModel(
            ceiling=ConfidenceClass.SYNTACTIC,
            default=ConfidenceClass.SYNTACTIC,
            allows_inferred=False,
            notes="openapi/fastapi=syntactic; express+regex=heuristic; "
            "consumer calls literal=syntactic, dynamic=heuristic",
        )

    def discovery_rules(self) -> tuple[DiscoveryRule, ...]:
        return (
            DiscoveryRule(
                glob="**/openapi.yaml", artifact_kind="openapi-spec",
                notes="canonical OpenAPI filename",
            ),
            DiscoveryRule(
                glob="**/openapi.json", artifact_kind="openapi-spec",
                notes="canonical OpenAPI filename (JSON)",
            ),
            DiscoveryRule(
                glob="**/*.yaml", artifact_kind="maybe-openapi",
                notes="content-checked for openapi/swagger keys",
            ),
            DiscoveryRule(
                glob="**/*.json", artifact_kind="maybe-openapi",
                notes="content-checked for openapi/swagger keys",
            ),
            DiscoveryRule(
                glob="**/*.py", artifact_kind="http-routes",
                notes="FastAPI decorators + python HTTP clients",
            ),
            DiscoveryRule(
                glob="**/*.ts", artifact_kind="http-routes",
                notes="Express routes + fetch/axios consumers",
            ),
        )

    # -- extract ---------------------------------------------------------

    def extract(
        self, request: DomainExtractionRequest
    ) -> DomainExtractionResult:
        sink = Emit(request, self.adapter_version)
        # Dois passes: consumidores rodam depois de specs+rotas para que
        # ``candidates`` de CALLS_ENDPOINT vejam TODOS os endpoints do
        # request — independente da ordem alfabética dos artefatos.
        deferred: list[Callable[[], None]] = []
        artifacts = sorted(request.artifacts, key=lambda a: a.path)
        for art in artifacts:
            ext = Path(art.path).suffix.lower()
            try:
                self._extract_one(sink, art.path, art.sha256, ext, deferred)
            except Exception as exc:  # isolamento por arquivo — nunca crash
                sink.diag(
                    "adapter-crash", "error",
                    f"{type(exc).__name__}: {exc}", art.path,
                )
        for fn in deferred:
            try:
                fn()
            except Exception as exc:  # mesma isolação por arquivo
                sink.diag(
                    "adapter-crash", "error",
                    f"deferred consumer pass: {type(exc).__name__}: {exc}",
                )
        return sink.result()

    # -- por artefato ------------------------------------------------------

    def _read(self, sink: Emit, path: str, sha256: str) -> tuple[str, str] | None:
        """Lê ``workspace_root/path``; devolve (texto, sha256) ou None."""
        # path do request é hostil — absoluto, ``..`` e symlink trocado
        # nunca saem da raiz do workspace (lf-c8 F7).
        root = self._root.resolve()
        fs = artifact_path_confined(path, root)
        if fs is None:
            sink.diag(
                "artifact-outside-root", "error",
                f"artifact path escapes repository root: {path}", path,
            )
            return None
        raw = read_file_verified(fs, root)
        if raw is None:
            sink.diag(
                "unreadable-artifact", "error",
                "cannot read artifact: unsafe or vanished", path,
            )
            return None
        if len(raw) > _MAX_ARTIFACT:
            sink.diag(
                "artifact-too-large", "error",
                f"{len(raw)} bytes exceeds {_MAX_ARTIFACT}", path,
            )
            return None
        real = hashlib.sha256(raw).hexdigest()
        if sha256 and sha256 != real:
            sink.diag(
                "artifact-hash-mismatch", "warning",
                f"inventory sha256 {sha256[:12]}… != computed {real[:12]}…",
                path,
            )
        return raw.decode("utf-8", "replace"), real

    def _extract_one(
        self,
        sink: Emit,
        path: str,
        sha256: str,
        ext: str,
        deferred: list[Callable[[], None]],
    ) -> None:
        got = self._read(sink, path, sha256)
        if got is None:
            return
        text, content_hash = got
        offsets = common.byte_offsets(text)
        if ext in (".yaml", ".yml"):
            self._extract_yaml(sink, path, text, offsets, content_hash)
        elif ext == ".json":
            self._extract_json(sink, path, text, offsets, content_hash)
        elif ext in _PY_EXTS:
            self._extract_py(sink, path, text, content_hash, deferred)
        elif ext in _TS_EXTS:
            self._extract_ts(sink, path, text, content_hash, deferred)
        elif ext in _JS_EXTS:
            self._extract_js(sink, path, text, content_hash, deferred)
        else:
            sink.diag(
                "unsupported-artifact", "info",
                f"extension {ext!r} not handled", path,
            )

    # -- specs -----------------------------------------------------------

    def _extract_yaml(
        self, sink: Emit, path: str, text: str,
        offsets: list[int], content_hash: str,
    ) -> None:
        doc = parse_yaml_subset(text)
        for issue in doc.issues:
            sink.diag(
                issue.code, issue.severity,
                f"line {issue.line}: {issue.message}" if issue.line
                else issue.message,
                path,
            )
        flavor = openapi.spec_flavor(doc.value)
        if flavor is None:
            if any(i.severity == "error" for i in doc.issues):
                sink.diag(
                    "malformed-spec", "error",
                    "YAML did not parse into an OpenAPI/Swagger document",
                    path,
                )
            else:
                sink.diag(
                    "not-openapi-artifact", "info",
                    "YAML is not an OpenAPI/Swagger document — skipped",
                    path,
                )
            return
        openapi.extract_spec(
            sink=sink, path=path, fmt="yaml", doc=doc.value,
            flavor=flavor,
            key_line=openapi.make_key_line(doc.key_lines),
            text=text, offsets=offsets, content_hash=content_hash,
        )
        sink.bump("openapi_specs")

    def _extract_json(
        self, sink: Emit, path: str, text: str,
        offsets: list[int], content_hash: str,
    ) -> None:
        try:
            doc = json.loads(text)
        except json.JSONDecodeError as exc:
            if '"openapi"' in text or '"swagger"' in text:
                sink.diag(
                    "malformed-spec", "error",
                    f"JSON looks like a spec but does not parse: {exc}",
                    path,
                )
            else:
                sink.diag(
                    "not-openapi-artifact", "info",
                    "JSON is not an OpenAPI/Swagger document — skipped",
                    path,
                )
            return
        flavor = openapi.spec_flavor(doc)
        if flavor is None:
            sink.diag(
                "not-openapi-artifact", "info",
                "JSON is not an OpenAPI/Swagger document — skipped",
                path,
            )
            return
        openapi.extract_spec(
            sink=sink, path=path, fmt="json", doc=doc,
            flavor=flavor,
            key_line=openapi.make_json_key_line(text),
            text=text, offsets=offsets, content_hash=content_hash,
        )
        sink.bump("openapi_specs")

    # -- código ------------------------------------------------------------

    def _parse_result(
        self, sink: Emit, path: str, text: str
    ) -> ParseResult | None:
        result = self._engine.parse(path, text.encode("utf-8"))
        for d in result.diagnostics:
            sink.diag(d.code, d.severity, d.message, path)
        if not result.supported:
            return None
        return result

    def _extract_py(
        self,
        sink: Emit,
        path: str,
        text: str,
        content_hash: str,
        deferred: list[Callable[[], None]],
    ) -> None:
        result = self._parse_result(sink, path, text)
        if result is None:
            return
        module = common.module_name(path, common.PY_MODULE_SUFFIXES)
        routes.extract_py_routes(
            sink=sink, path=path, text=text, result=result,
            module=module, content_hash=content_hash,
        )
        deferred.append(
            lambda: consumers.extract_consumers(
                sink=sink, path=path, text=text, result=result,
                module=module, content_hash=content_hash,
                table=consumers.PY_URL_ARG, lang="py",
                confidence=ConfidenceClass.SYNTACTIC,
            )
        )

    def _extract_ts(
        self,
        sink: Emit,
        path: str,
        text: str,
        content_hash: str,
        deferred: list[Callable[[], None]],
    ) -> None:
        result = self._parse_result(sink, path, text)
        if result is None:
            return
        module = common.module_name(path, common.TS_MODULE_SUFFIXES)
        routes.extract_ts_routes(
            sink=sink, path=path, text=text, result=result,
            module=module, content_hash=content_hash,
        )
        deferred.append(
            lambda: consumers.extract_consumers(
                sink=sink, path=path, text=text, result=result,
                module=module, content_hash=content_hash,
                table=consumers.TS_URL_ARG, lang="ts",
                confidence=ConfidenceClass.SYNTACTIC,
            )
        )

    def _extract_js(
        self,
        sink: Emit,
        path: str,
        text: str,
        content_hash: str,
        deferred: list[Callable[[], None]],
    ) -> None:
        module = common.module_name(path, common.TS_MODULE_SUFFIXES)
        routes.extract_js_routes(
            sink=sink, path=path, text=text,
            module=module, content_hash=content_hash,
        )
        deferred.append(
            lambda: consumers.extract_js_consumers(
                sink=sink, path=path, text=text,
                module=module, content_hash=content_hash,
            )
        )


def satisfies_protocol() -> DomainAdapter:
    """Smoke check para tests: a instância satisfaz ``DomainAdapter``."""
    adapter: DomainAdapter = ApiContractAdapter()
    return adapter
