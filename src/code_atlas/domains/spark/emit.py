"""Sink de emissão do adapter ``domains.spark`` (lf-0112, Phase 4).

Acumulador **por request** (não por arquivo): entidades dedupam por
``logical_key`` com evidência unida e o atributo ``roles`` fundido
(``read``/``write``); relações dedupam por ``(tipo, origem, alvo)``
acumulando evidência — mesma disciplina dos sinks ``sql``/``api``.

Também mantém os índices de linhagem do domínio:

- ``sources``: para cada transformação materializada, o conjunto de
  fontes terminais (tabelas/paths) que a alimentam — base do colapso
  ``sink DERIVES_FROM fonte`` nos writes;
- ``view_sources``: fontes terminais de temp views registradas —
  ``FROM v`` dentro de literais ``spark.sql`` colapsa além do alias.

Resolução honesta: alvo ``to_identity`` sai sem ``resolution`` (o
coordinator resolve pela ocorrência no snapshot); ``to_unresolved``
carrega ``DomainResolution(UNRESOLVED, syntactic)`` — nunca ``resolved``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from code_atlas.contracts.enums import ConfidenceClass, ResolutionStatus
from code_atlas.domains.contracts import (
    DomainDiagnostic,
    DomainExtractionRequest,
    DomainExtractionResult,
    ExtractedDomainEntity,
    ExtractedDomainRelation,
)
from code_atlas.domains.evidence import domain_extractor_id, evidence_record
from code_atlas.domains.identity import DomainIdentity
from code_atlas.domains.model import DomainEntityKind
from code_atlas.domains.resolution import DomainResolution
from code_atlas.parsers.contracts import EvidenceRecord, SourceSpan

DOMAIN_ID = "pyspark"

_DIAG_CAP = 60  # diagnósticos por request — depois vira resumo

_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*)://")
_BACKTICK_RE = re.compile(r"^`(.*)`$")


def split_table_name(literal: str) -> tuple[str, ...] | None:
    """``"cat.db.t"`` → partes; backticks por segmento são removidos.

    ``None`` quando o literal é vazio ou carrega marcador dinâmico
    (``${}``/``{{}}``) — o caller emite ``to_unresolved``, nunca fabrica.
    """
    lit = literal.strip()
    if not lit or "${" in lit or "{{" in lit:
        return None
    parts: list[str] = []
    for raw in lit.split("."):
        p = raw.strip()
        m = _BACKTICK_RE.match(p)
        if m:
            p = m.group(1)
        if not p:
            return None
        parts.append(p)
    return tuple(parts)


def uri_scheme(literal: str) -> str:
    """Esquema de URI do path (``s3a``, ``hdfs``, ``file``) ou ``""``."""
    m = _SCHEME_RE.match(literal.strip())
    return m.group(1).lower() if m else ""


@dataclass
class _EntAcc:
    entity: ExtractedDomainEntity
    ev: set[int] = field(default_factory=set)
    roles: set[str] = field(default_factory=set)
    # mesmo objeto dict passado ao ExtractedDomainEntity — mutável
    # (a entidade expõe Mapping; aqui vive a visão de escrita)
    attrs: dict[str, str] = field(default_factory=dict)


@dataclass
class _RelAcc:
    rel: ExtractedDomainRelation
    ev: set[int] = field(default_factory=set)


class Sink:
    """Acumulador determinístico da saída de ``extract``."""

    def __init__(
        self, request: DomainExtractionRequest, adapter_version: str
    ) -> None:
        self.request = request
        self.version = adapter_version
        self.entities: dict[str, _EntAcc] = {}
        self.relations: dict[tuple[str, str, str], _RelAcc] = {}
        self.evidence: list[EvidenceRecord] = []
        self._evidx: dict[tuple[str, int], int] = {}
        self.diagnostics: list[DomainDiagnostic] = []
        self._truncated = 0
        self.stats: dict[str, int] = {}
        self.sources: dict[str, set[str]] = {}
        self.view_sources: dict[str, set[str]] = {}
        self._diag_seen: set[tuple[str, str]] = set()

    # -- evidência / diagnóstico ----------------------------------------

    def evi(
        self,
        path: str,
        sha: str,
        span: SourceSpan | None,
        notes: str = "",
    ) -> int:
        """Evidência dedup por ``(path, start_byte)`` → índice no envelope."""
        key = (path, span.start_byte if span else -1)
        idx = self._evidx.get(key)
        if idx is not None:
            return idx
        self.evidence.append(
            evidence_record(
                adapter_domain=DOMAIN_ID,
                adapter_version=self.version,
                path=path,
                content_hash=sha,
                span=span,
                notes=notes,
            )
        )
        idx = len(self.evidence) - 1
        self._evidx[key] = idx
        return idx

    def diag(
        self, code: str, severity: str, message: str, path: str = ""
    ) -> None:
        if (code, message) in self._diag_seen:
            return
        self._diag_seen.add((code, message))
        if len(self.diagnostics) >= _DIAG_CAP:
            self._truncated += 1
            return
        self.diagnostics.append(
            DomainDiagnostic(
                code=code, severity=severity, message=message,
                path=path, adapter_id=DOMAIN_ID,
            )
        )

    def bump(self, stat: str, n: int = 1) -> None:
        self.stats[stat] = self.stats.get(stat, 0) + n

    def set_entity_attr(self, key: str, name: str, value: str) -> None:
        """Escreve atributo numa entidade já registrada (``logical_key``)."""
        acc = self.entities.get(key)
        if acc is not None:
            acc.attrs[name] = value

    # -- identidades ------------------------------------------------------

    def table_ident(self, parts: tuple[str, ...]) -> DomainIdentity:
        """catalog/namespace/qualified_name a partir das partes do nome."""
        req = self.request
        if len(parts) >= 3:
            catalog, namespace, name = (
                parts[0], ".".join(parts[1:-1]), parts[-1]
            )
        elif len(parts) == 2:
            catalog, namespace, name = "", parts[0], parts[1]
        else:
            catalog, namespace, name = "", "", parts[0]
        return DomainIdentity(
            domain=DOMAIN_ID, repository=req.repository, root=req.root,
            catalog=catalog, namespace=namespace,
            environment=req.environment, qualified_name=name,
        )

    def path_ident(self, literal: str) -> DomainIdentity:
        """Entidade de path (s3/hdfs/file) — NUNCA confundida com tabela.

        ``qualified_name`` é o literal verbatim (sem namespace/catalog
        de tabela); ``uri_scheme`` vai para os atributos da entidade.
        """
        req = self.request
        return DomainIdentity(
            domain=DOMAIN_ID, repository=req.repository, root=req.root,
            environment=req.environment, qualified_name=literal.strip(),
        )

    def transform_ident(self, path: str, anchor: str) -> DomainIdentity:
        """Entidade ``data_transformation`` — âncora ``<var>@<line>``."""
        req = self.request
        return DomainIdentity(
            domain=DOMAIN_ID, repository=req.repository, root=req.root,
            environment=req.environment,
            qualified_name=f"{path}#{anchor}",
        )

    def retag(self, ident: DomainIdentity) -> DomainIdentity:
        """Mesma posição de identidade, domínio ``pyspark``.

        Usado no remap da delegação ``spark.sql``: o adapter ``sql``
        emite ``domain="sql"`` — o contrato exige que tudo que este
        adapter emite carregue ``domain="pyspark"``.
        """
        return DomainIdentity(
            domain=DOMAIN_ID, repository=ident.repository,
            root=ident.root, catalog=ident.catalog,
            namespace=ident.namespace, environment=ident.environment,
            qualified_name=ident.qualified_name,
        )

    # -- entidades / relações --------------------------------------------

    def add_entity(
        self,
        ident: DomainIdentity,
        kind: DomainEntityKind,
        name: str,
        *,
        path: str = "",
        span: SourceSpan | None = None,
        content_hash: str = "",
        attributes: dict[str, str] | None = None,
        roles: tuple[str, ...] = (),
        evidence: tuple[int, ...] = (),
    ) -> DomainIdentity:
        """Registra entidade; dedup por ``logical_key`` funde evidência."""
        key = ident.logical_key()
        acc = self.entities.get(key)
        if acc is not None:
            acc.ev.update(evidence)
            acc.roles.update(roles)
            # primeira observação vence por chave (``via`` registra a
            # API que primeiro revelou a entidade); chaves novas entram
            for k, v in (attributes or {}).items():
                acc.attrs.setdefault(k, v)
            return ident
        attrs = dict(attributes or {})
        acc = _EntAcc(
            entity=ExtractedDomainEntity(
                identity=ident, kind=kind, name=name, path=path,
                span=span, content_hash=content_hash,
                attributes=attrs, evidence=(),
            ),
            ev=set(evidence),
            roles=set(roles),
            attrs=attrs,
        )
        self.entities[key] = acc
        return ident

    def add_rel(
        self,
        rtype: str,
        from_i: DomainIdentity,
        *,
        path: str,
        to_i: DomainIdentity | None = None,
        to_text: str = "",
        span: SourceSpan | None = None,
        evidence: tuple[int, ...] = (),
        resolution: DomainResolution | None = None,
    ) -> None:
        """Relação dedup por (tipo, origem, alvo); evidência acumula."""
        to_key = to_i.logical_key() if to_i is not None else f"text:{to_text}"
        key = (rtype, from_i.logical_key(), to_key)
        acc = self.relations.get(key)
        if acc is not None:
            acc.ev.update(evidence)
            return
        if resolution is None and to_i is None:
            resolution = DomainResolution(
                status=ResolutionStatus.UNRESOLVED,
                confidence_class=ConfidenceClass.SYNTACTIC,
                resolver=domain_extractor_id(DOMAIN_ID),
                unresolved_text=to_text,
            )
        self.relations[key] = _RelAcc(
            rel=ExtractedDomainRelation(
                relation_type=rtype, path=path,
                from_identity=from_i, to_identity=to_i,
                to_unresolved=to_text, resolution=resolution,
                span=span, evidence=(),
            ),
            ev=set(evidence),
        )

    # -- fechamento ---------------------------------------------------------

    def finish(self) -> None:
        if self._truncated:
            self.diagnostics.append(
                DomainDiagnostic(
                    code="diagnostics-truncated", severity="info",
                    message=(
                        f"{self._truncated} further diagnostics "
                        f"suppressed (cap {_DIAG_CAP})"
                    ),
                    path="", adapter_id=DOMAIN_ID,
                )
            )

    def result(self) -> DomainExtractionResult:
        entities = []
        for acc in self.entities.values():
            attrs = dict(acc.attrs)
            if acc.roles:
                attrs["roles"] = ",".join(sorted(acc.roles))
            entities.append(
                ExtractedDomainEntity(
                    identity=acc.entity.identity, kind=acc.entity.kind,
                    name=acc.entity.name, path=acc.entity.path,
                    span=acc.entity.span,
                    content_hash=acc.entity.content_hash,
                    attributes=attrs, evidence=tuple(sorted(acc.ev)),
                )
            )
        relations = [
            ExtractedDomainRelation(
                relation_type=acc.rel.relation_type, path=acc.rel.path,
                from_identity=acc.rel.from_identity,
                from_symbol=acc.rel.from_symbol,
                to_identity=acc.rel.to_identity,
                to_symbol=acc.rel.to_symbol,
                to_unresolved=acc.rel.to_unresolved,
                resolution=acc.rel.resolution,
                span=acc.rel.span, evidence=tuple(sorted(acc.ev)),
            )
            for acc in self.relations.values()
        ]
        return DomainExtractionResult(
            domain_id=DOMAIN_ID,
            adapter_version=self.version,
            entities=tuple(entities),
            relations=tuple(relations),
            evidence=tuple(self.evidence),
            diagnostics=tuple(self.diagnostics),
            stats=dict(self.stats),
        )
