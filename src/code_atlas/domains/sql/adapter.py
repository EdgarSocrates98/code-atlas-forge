"""``SqlDomainAdapter`` — adapter de domínio SQL (lf-0111, Cycle 7 Ph.3).

Linhagem estática ANSI/Athena(Trino)/Spark sobre artefatos ``.sql``:

- tokenizer + parser stdlib puros (``tokens.py``/``parser.py``) —
  **zero dependências**, nenhum ``sqlparse``;
- extração por statement em ``extract.py``: ``DataTable``/``DataView``
  com identidade catalog/schema/name-qualificada, ``data_column`` para
  linhagem de coluna e ``data_transformation`` (1 por statement) como
  âncora honesta de ``READS_FROM``/``WRITES_TO``/``TRANSFORMS``;
- ``DERIVES_FROM`` tabela→tabela colapsa CTEs/subqueries/views;
  ``DERIVES_FROM`` coluna→coluna só quando a expressão resolve
  honestamente (alias único, sem star, sem fonte opaca);
- estados honestos: ``SELECT *``, aliases não-resolvíveis, SQL
  dinâmico (``${}``/``{{}}``/``EXEC``) e fontes opacas viram
  ``DomainDiagnostic`` ou ``to_unresolved`` — nunca ``resolved``;
- dialeto é **rótulo** de discovery (``detect_dialect``), declarado em
  ``discovery_rules`` — não é gate de extração.

Leitura de artefatos (mesmo desenho de ``ApiContractAdapter``/
``TerraformAdapter``): ``base_path`` resolve os paths relativos de
``DiscoveredArtifact``; ``None`` trata os paths como estão. O adapter
nunca lê fora do request, nunca executa SQL/jobs e nunca abre rede.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from code_atlas.contracts.enums import ConfidenceClass
from code_atlas.domains.contracts import (
    ArtifactSupport,
    ConfidenceModel,
    DiscoveryRule,
    DomainDiagnostic,
    DomainExtractionRequest,
    DomainExtractionResult,
    ExtractedDomainEntity,
    ExtractedDomainRelation,
)
from code_atlas.domains.model import DomainEntityKind, DomainRelationKind
from code_atlas.domains.sql.extract import FileExtraction, extract_parsed
from code_atlas.domains.sql.parser import parse_file
from code_atlas.parsers.contracts import EvidenceRecord

DOMAIN_ID = "sql"
ADAPTER_VERSION = "0.1.0"

_MAX_ARTIFACT = 4 << 20  # 4 MiB — mesmo guarda-rail do adapter api

# Content-sniffing honesto: um artefato listado no request cuja extensão
# não é ``.sql`` só é extraído quando o conteúdo realmente parece SQL.
# Extensão ``.sql`` sem conteúdo SQL vira diagnóstico — nunca claim falso.
_SNIFF_RE = re.compile(
    r"^\s*(?:--[^\n]*\n|/\*.*?\*/\s*)*"
    r"(?:WITH|SELECT|INSERT|UPDATE|DELETE|MERGE|CREATE|USE|TRUNCATE|"
    r"DROP|ALTER|PREPARE|EXECUTE|EXPLAIN|GRANT)\b",
    re.IGNORECASE | re.DOTALL,
)


def looks_like_sql(text: str) -> bool:
    """Heurística de sniffing — keyword inicial após comentários."""
    return bool(_SNIFF_RE.match(text[:8192]))


class SqlDomainAdapter:
    """Adapter ``sql`` — linhagem estática ANSI/Athena/Spark.

    ``base_path``: raiz on-disk que resolve os paths relativos de
    ``DiscoveredArtifact``; ``None`` trata os paths como estão
    (absolutos ou relativos ao cwd do caller).
    """

    def __init__(self, base_path: str | Path | None = None) -> None:
        self._base = Path(base_path) if base_path is not None else None

    # -- contrato ------------------------------------------------------

    @property
    def domain_id(self) -> str:
        return DOMAIN_ID

    @property
    def adapter_version(self) -> str:
        return ADAPTER_VERSION

    def supported_artifacts(self) -> ArtifactSupport:
        return ArtifactSupport(
            extensions=frozenset({".sql"}),
            globs=("*.sql.j2",),
        )

    def entity_kinds(self) -> frozenset[DomainEntityKind]:
        return frozenset(
            {
                DomainEntityKind.DATA_TABLE,
                DomainEntityKind.DATA_VIEW,
                DomainEntityKind.DATA_COLUMN,
                DomainEntityKind.DATA_TRANSFORMATION,
            }
        )

    def relation_kinds(self) -> frozenset[str]:
        return frozenset(
            {
                str(DomainRelationKind.READS_FROM),
                str(DomainRelationKind.WRITES_TO),
                str(DomainRelationKind.TRANSFORMS),
                str(DomainRelationKind.DERIVES_FROM),
            }
        )

    def confidence_model(self) -> ConfidenceModel:
        # Extração estritamente evidence-first: o que o texto SQL não
        # estabelece sai como diagnóstico/unresolved — nunca promovido.
        return ConfidenceModel(
            ceiling=ConfidenceClass.SYNTACTIC,
            default=ConfidenceClass.SYNTACTIC,
            allows_inferred=False,
            notes="static SQL lineage; star/ambiguous/dynamic are "
            "diagnosed, never resolved",
        )

    def discovery_rules(self) -> tuple[DiscoveryRule, ...]:
        return (
            DiscoveryRule(
                glob="*.sql",
                artifact_kind="sql-script",
                notes=(
                    "ANSI/Athena(Trino)/Spark SQL scripts — dialect is "
                    "content-sniffed (spark|athena|ansi label), not a gate"
                ),
            ),
            DiscoveryRule(
                glob="*.sql.j2",
                artifact_kind="sql-template",
                notes=(
                    "templated SQL — parsed best-effort; dynamic markers "
                    "produce diagnostics, never fabricated relations"
                ),
            ),
        )

    # -- extract ---------------------------------------------------------

    def extract(
        self, request: DomainExtractionRequest
    ) -> DomainExtractionResult:
        try:
            return _extract(request, self.adapter_version, self._base)
        except Exception as exc:  # última linha de defesa — nunca levanta
            return DomainExtractionResult(
                domain_id=self.domain_id,
                adapter_version=self.adapter_version,
                diagnostics=(
                    DomainDiagnostic(
                        code="adapter-failure",
                        severity="error",
                        message=f"sql adapter failure: {exc!r}",
                        adapter_id=self.domain_id,
                    ),
                ),
            )

    # -- por artefato ----------------------------------------------------

    def _read(
        self, path: str, sha256: str, diags: list[DomainDiagnostic]
    ) -> tuple[str, str] | None:
        """Lê ``base_path/path``; devolve (texto, sha256) ou None."""
        fs = Path(path) if self._base is None else self._base / path
        try:
            raw = fs.read_bytes()
        except OSError as exc:
            diags.append(
                DomainDiagnostic(
                    code="unreadable-artifact", severity="error",
                    message=f"cannot read artifact: {exc}",
                    path=path, adapter_id=DOMAIN_ID,
                )
            )
            return None
        if len(raw) > _MAX_ARTIFACT:
            diags.append(
                DomainDiagnostic(
                    code="artifact-too-large", severity="error",
                    message=f"{len(raw)} bytes exceeds {_MAX_ARTIFACT}",
                    path=path, adapter_id=DOMAIN_ID,
                )
            )
            return None
        real = hashlib.sha256(raw).hexdigest()
        if sha256 and sha256 != real:
            diags.append(
                DomainDiagnostic(
                    code="artifact-hash-mismatch", severity="warning",
                    message=(
                        f"inventory sha256 {sha256[:12]}… != "
                        f"computed {real[:12]}…"
                    ),
                    path=path, adapter_id=DOMAIN_ID,
                )
            )
        return raw.decode("utf-8", "replace"), real


def _extract(
    request: DomainExtractionRequest,
    adapter_version: str,
    base: Path | None,
) -> DomainExtractionResult:
    """Extrai o request inteiro — merge determinístico por artefato."""
    entities: dict[str, ExtractedDomainEntity] = {}
    relations: list[ExtractedDomainRelation] = []
    evidence: list[EvidenceRecord] = []
    diagnostics: list[DomainDiagnostic] = []
    stats: dict[str, int] = {}

    adapter = SqlDomainAdapter(base)
    for art in sorted(request.artifacts, key=lambda a: a.path):
        got = adapter._read(art.path, art.sha256, diagnostics)
        if got is None:
            continue
        text, content_hash = got
        ext = Path(art.path).suffix.lower()
        if ext != ".sql" and not looks_like_sql(text):
            diagnostics.append(
                DomainDiagnostic(
                    code="not-sql-artifact", severity="info",
                    message="content does not look like SQL — skipped",
                    path=art.path, adapter_id=DOMAIN_ID,
                )
            )
            continue
        try:
            parsed = parse_file(text)
            fx = extract_parsed(
                parsed,
                repository=request.repository,
                root=request.root,
                environment=request.environment,
                path=art.path,
                sha=content_hash,
                adapter_version=adapter_version,
            )
        except Exception as exc:  # isolamento por arquivo — nunca crash
            diagnostics.append(
                DomainDiagnostic(
                    code="adapter-crash", severity="error",
                    message=f"{type(exc).__name__}: {exc}",
                    path=art.path, adapter_id=DOMAIN_ID,
                )
            )
            continue
        if (
            not fx.entities and not fx.relations
            and not parsed.statements and not looks_like_sql(text)
        ):
            diagnostics.append(
                DomainDiagnostic(
                    code="not-sql-artifact", severity="info",
                    message=".sql artifact with no SQL statements",
                    path=art.path, adapter_id=DOMAIN_ID,
                )
            )
            continue
        _merge_file(
            fx, entities=entities, relations=relations,
            evidence=evidence, diagnostics=diagnostics, stats=stats,
        )

    return DomainExtractionResult(
        domain_id=DOMAIN_ID,
        adapter_version=adapter_version,
        entities=tuple(entities.values()),
        relations=tuple(relations),
        evidence=tuple(evidence),
        diagnostics=tuple(diagnostics),
        stats=stats,
    )


def _merge_file(
    fx: FileExtraction,
    *,
    entities: dict[str, ExtractedDomainEntity],
    relations: list[ExtractedDomainRelation],
    evidence: list[EvidenceRecord],
    diagnostics: list[DomainDiagnostic],
    stats: dict[str, int],
) -> None:
    """Funde a extração de um artefato no resultado do request.

    Índices de evidência são relativos ao arquivo — remapeados para o
    envelope global. Entidades dedupam por ``logical_key``; declaração
    (``declared=create``) vence referência no merge de atributos e a
    evidência acumula — mesma política do dedup intra-arquivo.
    """
    base = len(evidence)
    evidence.extend(fx.evidence)

    def remap(evi: tuple[int, ...]) -> tuple[int, ...]:
        return tuple(i + base for i in evi)

    for e in fx.entities:
        key = e.identity.logical_key()
        prev = entities.get(key)
        if prev is None:
            entities[key] = ExtractedDomainEntity(
                identity=e.identity, kind=e.kind, name=e.name,
                path=e.path, span=e.span, content_hash=e.content_hash,
                attributes=e.attributes, evidence=remap(e.evidence),
            )
            continue
        merged_ev = tuple(sorted({*prev.evidence, *remap(e.evidence)}))
        declared_wins = (
            e.attributes.get("declared") == "create"
            and prev.attributes.get("declared") != "create"
        )
        if declared_wins:
            entities[key] = ExtractedDomainEntity(
                identity=e.identity, kind=e.kind, name=e.name,
                path=e.path, span=e.span, content_hash=e.content_hash,
                attributes=e.attributes, evidence=merged_ev,
            )
        else:
            entities[key] = ExtractedDomainEntity(
                identity=prev.identity, kind=prev.kind, name=prev.name,
                path=prev.path, span=prev.span,
                content_hash=prev.content_hash,
                attributes=prev.attributes, evidence=merged_ev,
            )

    relations.extend(
        ExtractedDomainRelation(
            relation_type=r.relation_type, path=r.path,
            from_identity=r.from_identity, from_symbol=r.from_symbol,
            to_identity=r.to_identity, to_symbol=r.to_symbol,
            to_unresolved=r.to_unresolved, resolution=r.resolution,
            span=r.span, evidence=remap(r.evidence),
        )
        for r in fx.relations
    )
    diagnostics.extend(fx.diagnostics)
    for k, v in fx.stats.items():
        stats[k] = stats.get(k, 0) + v


def satisfies_protocol() -> SqlDomainAdapter:
    """Smoke check para tests: a instância satisfaz ``DomainAdapter``."""
    adapter: SqlDomainAdapter = SqlDomainAdapter()
    return adapter
