"""Faceta de busca sobre entidades de domínio (lf-0119, Cycle 7 Ph11).

``domain_entities`` não tem FTS dedicado — ``fts_occurrences`` cobre
somente símbolos de código — então a correspondência é LIKE escapado
**bounded** sobre ``domain_entity_occurrences`` ancoradas no snapshot
(``name``/``qualified_name``/``path``), com teto de candidatos
declarado. Nunca se funde com hits de símbolo: sai como faceta
separada ``domain_items`` no envelope, um item por entidade lógica,
com a contagem de evidência (ocorrências no snapshot).

Estados honestos declarados via ``SearchDiagnostic``:

- tabela de domínio ausente (schema <v9) → faceta omitida +
  ``domain-facet-unavailable``;
- filtro ``language`` ativo → entidades de domínio não carregam
  linguagem: faceta omitida + ``domain-facet-skipped`` (nunca um
  narrowing silencioso/falso);
- mais candidatos que o teto de scan → ``domain-facet-capped`` com o
  total real;
- expansão ``via_relation`` cortada pelo teto de arestas →
  ``domain-related-capped``.

``via_relation`` (relationship search mínimo): entidades que são
endpoint entity↔entity **resolvido** de ``domain_relations`` adjacente
a uma entidade que casou com ``q`` — a aresta real é citada em
``evidence`` como ``via=TYPE:rel:<id>``. Nenhuma aresta é inventada:
só rows persistidos do snapshot. Sem match direto → nenhuma expansão.
"""

from __future__ import annotations

import json
import sqlite3

from code_atlas.search.contracts import (
    DomainSearchHit,
    SearchDiagnostic,
    SearchRequest,
)

# Tetos honestos da faceta — declarados em diagnóstico quando batidos.
_CANDIDATE_CAP = 200   # entidades candidatas escaneadas (match direto)
_RELATED_EDGE_CAP = 500  # arestas entity↔entity lidas na expansão
_MAX_LIMIT = 200
_RELATED_SCORE = 0.5
_DIRECT_SCORE = 1.0
_EXACT_SCORE = 2.0

# Query agrupada por entidade: flags de match como agregados MAX(CASE)
# (honestas por construção); ``de.*`` são chave do GROUP BY (PK →
# constantes por grupo). path/line/extraction_status da ocorrência
# representativa (MIN deo.id) são hidratados por query separada —
# SQLite só garante bare-column↔MIN com UM único agregado min/max.
_DIRECT_SELECT = """
SELECT de.id AS entity_id, de.domain, de.kind, de.identity_key,
       de.qualified_name, de.name, de.catalog, de.namespace,
       de.environment,
       COUNT(*) AS occurrences,
       MIN(deo.id) AS first_occurrence_id,
       MAX(CASE WHEN sf.relative_path LIKE ? ESCAPE '\\'
                THEN 1 ELSE 0 END) AS m_path,
       MAX(de.name = ? OR de.qualified_name = ?) AS m_exact
  FROM domain_entities de
  JOIN domain_entity_occurrences deo
    ON deo.entity_id = de.id AND deo.snapshot_id = ?
  LEFT JOIN source_files sf ON sf.id = deo.source_file_id
"""


def _esc_like(term: str) -> str:
    """Escapa curingas LIKE — input do usuário nunca vira padrão."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class DomainEntitySearch:
    """Faceta bounded-LIKE de entidades de domínio num snapshot."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._available: bool | None = None

    def available(self) -> bool:
        """Schema >=v9 presente — cacheada por instância."""
        if self._available is None:
            self._available = self._conn.execute(
                "SELECT 1 FROM sqlite_master"
                " WHERE name='domain_entity_occurrences'"
            ).fetchone() is not None
        return self._available

    def search(
        self,
        req: SearchRequest,
        repository_id: int,
        snapshot_id: int,
        *,
        include_related: bool = True,
    ) -> tuple[tuple[DomainSearchHit, ...], int, tuple[SearchDiagnostic, ...]]:
        """``(items, total, diagnostics)`` — faceta top-``limit`` honesta.

        ``total`` = matches diretos (COUNT real, não o teto de scan) +
        entidades adjacentes ``via_relation`` encontradas.
        """
        diags: list[SearchDiagnostic] = []
        if not self.available():
            return (), 0, (SearchDiagnostic(
                code="domain-facet-unavailable", severity="info",
                message="tabelas de domínio ausentes (schema <v9)"),)
        if req.language:
            return (), 0, (SearchDiagnostic(
                code="domain-facet-skipped", severity="info",
                message="filtro language não se aplica a entidades de "
                        "domínio — faceta omitida"),)
        q = req.query.strip()
        if not q:
            return (), 0, ()
        limit = max(1, min(req.limit or 20, _MAX_LIMIT))

        rows, total, capped = self._direct_rows(
            req, repository_id, snapshot_id, q)
        if capped:
            diags.append(SearchDiagnostic(
                code="domain-facet-capped", severity="info",
                message=f"faceta de domínio escaneou os primeiros "
                        f"{_CANDIDATE_CAP} de {total} matches diretos"))
        direct_ids = [int(r["entity_id"]) for r in rows]
        occ_paths = self._representative_occurrences(
            snapshot_id, [int(r["first_occurrence_id"]) for r in rows])

        hits: list[DomainSearchHit] = []
        for r in rows:
            occ = occ_paths.get(int(r["first_occurrence_id"]), {})
            occ_line = occ.get("line")
            fields: list[str] = []
            if q.lower() in str(r["name"]).lower():
                fields.append("name")
            if q.lower() in str(r["qualified_name"]).lower():
                fields.append("qualified_name")
            if int(r["m_path"]):
                fields.append("path")
            exact = bool(r["m_exact"])
            hits.append(DomainSearchHit(
                entity_id=int(r["entity_id"]),
                occurrence_id=int(r["first_occurrence_id"]),
                domain=str(r["domain"]), kind=str(r["kind"]),
                qualified_name=str(r["qualified_name"]),
                name=str(r["name"]),
                identity_key=str(r["identity_key"]),
                catalog=str(r["catalog"]),
                namespace=str(r["namespace"]),
                environment=str(r["environment"]),
                path=str(occ.get("path") or ""),
                line=(occ_line if isinstance(occ_line, int) else None),
                occurrences=int(r["occurrences"]),
                extraction_status=str(occ.get("status") or ""),
                repository_id=repository_id,
                snapshot_id=snapshot_id,
                score=_EXACT_SCORE if exact else _DIRECT_SCORE,
                matched_fields=tuple(fields),
                evidence=(f"de:{int(r['entity_id'])}",),
            ))

        related_total = 0
        if include_related and hits:
            related, rel_diags = self._related_hits(
                req, repository_id, snapshot_id, set(direct_ids))
            diags.extend(rel_diags)
            related_total = len(related)
            hits.extend(related)

        # Ordem determinística: exato > direto > via_relation, depois
        # identidade da entidade — nunca depende de ordem de scan.
        hits.sort(key=lambda h: (
            -h.score, h.domain, h.kind, h.qualified_name, h.entity_id))
        return tuple(hits[:limit]), total + related_total, tuple(diags)

    # --- match direto -------------------------------------------------

    def _direct_rows(
        self,
        req: SearchRequest,
        repository_id: int,
        snapshot_id: int,
        q: str,
    ) -> tuple[list[sqlite3.Row], int, bool]:
        """Candidatos LIKE bounded — ``(rows, total_real, capped)``."""
        like = f"%{_esc_like(q)}%"
        conds = [
            "(de.name LIKE ? ESCAPE '\\'"
            " OR de.qualified_name LIKE ? ESCAPE '\\'"
            " OR sf.relative_path LIKE ? ESCAPE '\\')",
        ]
        params: list[object] = [like, like, like]
        if req.domain:
            conds.append("de.domain = ?")
            params.append(req.domain)
        if req.kind:
            conds.append("de.kind = ?")
            params.append(req.kind)
        if req.path:
            conds.append("sf.relative_path LIKE ? ESCAPE '\\'")
            params.append(f"%{_esc_like(req.path)}%")
        where = " AND ".join(conds)
        base = _DIRECT_SELECT + f" WHERE de.repository_id = ? AND {where}"
        # SELECT params (m_path like, m_exact q) precedem os do WHERE.
        sel_params = [like, q, q, snapshot_id, repository_id, *params]
        # exact-match primeiro — o teto de scan nunca esconde o melhor
        # candidato; desempate pela identidade da entidade.
        list_sql = (
            base + " GROUP BY de.id"
            " ORDER BY m_exact DESC, de.domain, de.kind,"
            " de.qualified_name, de.id LIMIT ?"
        )
        count_sql = (
            "SELECT COUNT(DISTINCT de.id) AS n "
            "FROM domain_entities de "
            "JOIN domain_entity_occurrences deo"
            "  ON deo.entity_id = de.id AND deo.snapshot_id = ? "
            "LEFT JOIN source_files sf ON sf.id = deo.source_file_id "
            f"WHERE de.repository_id = ? AND {where}"
        )
        rows = self._conn.execute(
            list_sql, [*sel_params, _CANDIDATE_CAP]).fetchall()
        total = int(self._conn.execute(
            count_sql, [snapshot_id, repository_id, *params]
        ).fetchone()["n"])
        return rows, total, total > _CANDIDATE_CAP

    def _representative_occurrences(
        self, snapshot_id: int, occ_ids: list[int]
    ) -> dict[int, dict[str, object]]:
        """path/line/status da ocorrência representativa (MIN deo.id)."""
        if not occ_ids:
            return {}
        rows = self._conn.execute(
            "SELECT deo.id, sf.relative_path AS path, deo.line,"
            " deo.extraction_status AS status"
            " FROM domain_entity_occurrences deo"
            " LEFT JOIN source_files sf ON sf.id = deo.source_file_id"
            " WHERE deo.id IN (SELECT value FROM json_each(?))",
            (json.dumps(occ_ids),),
        ).fetchall()
        return {int(r["id"]): dict(r) for r in rows}

    # --- expansão via_relation (resolved targets de domain_relations) --

    def _related_hits(
        self,
        req: SearchRequest,
        repository_id: int,
        snapshot_id: int,
        direct_ids: set[int],
    ) -> tuple[list[DomainSearchHit], tuple[SearchDiagnostic, ...]]:
        """Entidades adjacentes a matches diretos por arestas REAIS.

        Lê ``domain_relations`` do snapshot com ambos os endpoints
        entity-occurrence resolvidos; o "outro lado" de uma aresta que
        toca um match direto entra como hit ``via_relation`` —
        deduplicado contra os matches diretos e contra si mesmo
        (primeira aresta por ordem de ``r.id`` declara a proveniência).
        A expansão é por aresta, nunca por nome — ``q`` já casou.
        """
        diags: list[SearchDiagnostic] = []
        ids_json = json.dumps(sorted(direct_ids))
        edge_rows = self._conn.execute(
            "SELECT r.id, r.type, fe.entity_id AS from_entity,"
            " te.entity_id AS to_entity"
            " FROM domain_relations r"
            " JOIN domain_entity_occurrences fe"
            "   ON fe.id = r.from_entity_occ_id"
            " JOIN domain_entity_occurrences te"
            "   ON te.id = r.to_entity_occ_id"
            " WHERE r.snapshot_id = ? AND ("
            " fe.entity_id IN (SELECT value FROM json_each(?))"
            " OR te.entity_id IN (SELECT value FROM json_each(?)))"
            " ORDER BY r.id LIMIT ?",
            (snapshot_id, ids_json, ids_json, _RELATED_EDGE_CAP),
        ).fetchall()
        if len(edge_rows) >= _RELATED_EDGE_CAP:
            diags.append(SearchDiagnostic(
                code="domain-related-capped", severity="info",
                message=f"expansão via_relation leu o teto de "
                        f"{_RELATED_EDGE_CAP} arestas"))
        via: dict[int, str] = {}
        for r in edge_rows:
            fe, te = int(r["from_entity"]), int(r["to_entity"])
            other = te if fe in direct_ids else fe
            if other in direct_ids or other in via:
                continue
            via[other] = f"via={r['type']}:rel:{int(r['id'])}"
        if not via:
            return [], tuple(diags)
        extra_conds = ""
        params: list[object] = [snapshot_id, repository_id,
                                json.dumps(sorted(via))]
        if req.domain:
            extra_conds += " AND de.domain = ?"
            params.append(req.domain)
        if req.kind:
            extra_conds += " AND de.kind = ?"
            params.append(req.kind)
        related_sql = (
            "SELECT de.id AS entity_id, de.domain, de.kind,"
            " de.identity_key, de.qualified_name, de.name, de.catalog,"
            " de.namespace, de.environment,"
            " COUNT(*) AS occurrences,"
            " MIN(deo.id) AS first_occurrence_id,"
            " sf.relative_path AS path, deo.line AS line,"
            " deo.extraction_status AS status"
            " FROM domain_entities de"
            " JOIN domain_entity_occurrences deo"
            "   ON deo.entity_id = de.id AND deo.snapshot_id = ?"
            " LEFT JOIN source_files sf ON sf.id = deo.source_file_id"
            " WHERE de.repository_id = ?"
            " AND de.id IN (SELECT value FROM json_each(?))"
            + extra_conds
            + " GROUP BY de.id"
            " ORDER BY de.domain, de.kind, de.qualified_name, de.id"
        )
        rows = self._conn.execute(related_sql, params).fetchall()
        hits = [
            DomainSearchHit(
                entity_id=int(r["entity_id"]),
                occurrence_id=int(r["first_occurrence_id"]),
                domain=str(r["domain"]), kind=str(r["kind"]),
                qualified_name=str(r["qualified_name"]),
                name=str(r["name"]),
                identity_key=str(r["identity_key"]),
                catalog=str(r["catalog"]),
                namespace=str(r["namespace"]),
                environment=str(r["environment"]),
                path=str(r["path"] or ""),
                line=(None if r["line"] is None else int(r["line"])),
                occurrences=int(r["occurrences"]),
                extraction_status=str(r["status"] or ""),
                repository_id=repository_id,
                snapshot_id=snapshot_id,
                score=_RELATED_SCORE,
                matched_fields=("via_relation",),
                evidence=(f"de:{int(r['entity_id'])}",
                          via[int(r["entity_id"])]),
            )
            for r in rows
        ]
        return hits, tuple(diags)
