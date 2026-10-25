"""Faceta de busca sobre entidades de domínio (lf-0119, Cycle 7 Ph11;
FTS dedicado em lf-0133, Cycle 8 Phase 7).

A correspondência direta usa ``domain_fts`` (schema v12) quando a
tabela existe **e** cobre o snapshot consultado — BM25 ordena os
candidatos dentro do teto de scan. Sem cobertura (banco antigo,
snapshot pré-v12, build sem FTS5, query não tokenizável) cai no LIKE
escapado **bounded** de sempre sobre ``domain_entity_occurrences``
ancoradas no snapshot — nunca resultado vazio silencioso: a queda é
declarada via diagnóstico. Nunca se funde com hits de símbolo: sai
como faceta separada ``domain_items`` no envelope, um item por
entidade lógica, com a contagem de evidência (ocorrências no snapshot).

Semântica honesta de cada caminho (diferenças declaradas, não
escondidas): LIKE é match de substring case-insensitive ASCII; FTS5
``unicode61`` casa por token com case-folding e remoção de diacríticos
(``sao`` casa ``São`` no FTS, não no LIKE) e a coluna ``terms`` cobre
fragmentos de identificador (``orders`` casa ``v_orders`` por token).

Estados honestos declarados via ``SearchDiagnostic``:

- tabela de domínio ausente (schema <v9) → faceta omitida +
  ``domain-facet-unavailable``;
- filtro ``language`` ativo → entidades de domínio não carregam
  linguagem: faceta omitida + ``domain-facet-skipped`` (nunca um
  narrowing silencioso/falso);
- ``domain_fts`` existe mas não cobre o snapshot → LIKE bounded +
  ``domain-fts-fallback``;
- MATCH FTS inválida → ``domain-fts-error`` + queda para LIKE;
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
from code_atlas.search.tokenizer import fts_escape, split_identifier

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

# Pesos bm25 por coluna de ``domain_fts`` (ordem do CREATE v12):
# snapshot_id, entity_id, name, qualified_name, terms, path, domain,
# kind → 8 posições. ``name``/``qualified_name`` guardam a forma exata
# (peso maior); ``terms`` cobre fragmentos tokenizados; ``path`` é
# âncora fraca; escopos UNINDEXED não pontuam.
_FTS_BM25_WEIGHTS = "0.0, 0.0, 10.0, 8.0, 6.0, 1.0, 0.0, 0.0"

# Mesma projeção agregada de _DIRECT_SELECT, dirigida por MATCH: as
# rows candidatas vêm do índice FTS (rowid = domain_entity_occurrences.id)
# e ``de`` hidrata a entidade — o LIKE de path opera sobre a coluna
# armazenada ``f.path`` (mesma semântica de ``sf.relative_path``).
# ``bm25`` é função auxiliar do FTS5 e não pode viver dentro de
# agregado — a subquery com ``LIMIT -1`` (impede flattening) projeta o
# rank por row e o agregado externo toma ``MIN(f.bm)``.
_FTS_SELECT = (
    "SELECT de.id AS entity_id, de.domain, de.kind, de.identity_key,"
    " de.qualified_name, de.name, de.catalog, de.namespace,"
    " de.environment,"
    " COUNT(*) AS occurrences,"
    " MIN(f.rowid) AS first_occurrence_id,"
    " MAX(CASE WHEN f.path LIKE ? ESCAPE '\\'"
    "          THEN 1 ELSE 0 END) AS m_path,"
    " MAX(de.name = ? OR de.qualified_name = ?) AS m_exact,"
    " MIN(f.bm) AS m_rank"
    " FROM (SELECT rowid, entity_id, path,"
    f" bm25(domain_fts, {_FTS_BM25_WEIGHTS}) AS bm"
    " FROM domain_fts"
    " WHERE domain_fts MATCH ? AND snapshot_id = ? LIMIT -1) f"
    " JOIN domain_entities de ON de.id = f.entity_id "
)


def _esc_like(term: str) -> str:
    """Escapa curingas LIKE — input do usuário nunca vira padrão."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _fts_match(query: str) -> str | None:
    """Expressão MATCH para ``domain_fts`` — ``None`` quando não tokenizável.

    Mesmo desenho de ``lexical.fts_query``: fragmentos por
    ``split_identifier`` em OR nas colunas indexadas + formas exatas em
    ``name``/``qualified_name``; sufixo ``*`` vira prefixo em ``terms``.
    """
    q = query.strip()
    if not q:
        return None
    if q.endswith("*") and len(q) > 1:
        stem = q[:-1]
        toks = split_identifier(stem)
        return f"terms : {fts_escape(toks[-1] if toks else stem)}*"
    toks = split_identifier(q)
    if not toks:
        return None
    or_terms = " OR ".join(fts_escape(t) for t in toks)
    return (
        f"{{name qualified_name terms path}} : ({or_terms})"
        f" OR name : {fts_escape(q)}"
        f" OR qualified_name : {fts_escape(q)}"
    )


class DomainEntitySearch:
    """Faceta de entidades de domínio — FTS preferido, LIKE bounded."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._available: bool | None = None
        self._fts_present: bool | None = None
        self._fts_covered: dict[int, bool] = {}

    def available(self) -> bool:
        """Schema >=v9 presente — cacheada por instância."""
        if self._available is None:
            self._available = self._conn.execute(
                "SELECT 1 FROM sqlite_master"
                " WHERE name='domain_entity_occurrences'"
            ).fetchone() is not None
        return self._available

    def _fts_usable_for(self, snapshot_id: int) -> bool:
        """``domain_fts`` presente E cobrindo ``snapshot_id``.

        Cobertura é por snapshot: só o persist pós-v12 grava rows —
        snapshots antigas seguem no LIKE honesto. Cacheado por
        instância (uma SearchEngine por request nas superfícies).
        """
        if self._fts_present is None:
            self._fts_present = self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='domain_fts'"
            ).fetchone() is not None
        if not self._fts_present:
            return False
        if snapshot_id not in self._fts_covered:
            self._fts_covered[snapshot_id] = self._conn.execute(
                "SELECT 1 FROM domain_fts WHERE snapshot_id = ? LIMIT 1",
                (snapshot_id,),
            ).fetchone() is not None
        return self._fts_covered[snapshot_id]

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

        rows, total, capped, fb_diag, used_fts = self._direct_rows(
            req, repository_id, snapshot_id, q)
        if fb_diag is not None:
            diags.append(fb_diag)
        if capped:
            diags.append(SearchDiagnostic(
                code="domain-facet-capped", severity="info",
                message=f"faceta de domínio escaneou os primeiros "
                        f"{_CANDIDATE_CAP} de {total} matches diretos"))
        direct_ids = [int(r["entity_id"]) for r in rows]
        occ_paths = self._representative_occurrences(
            snapshot_id, [int(r["first_occurrence_id"]) for r in rows])

        hits = [
            self._direct_hit(
                r, q, occ_paths.get(int(r["first_occurrence_id"]), {}),
                repository_id, snapshot_id, used_fts)
            for r in rows
        ]

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

    @staticmethod
    def _direct_hit(
        r: sqlite3.Row,
        q: str,
        occ: dict[str, object],
        repository_id: int,
        snapshot_id: int,
        used_fts: bool,
    ) -> DomainSearchHit:
        """Hit direto — ``matched_fields`` honesto sobre COMO casou."""
        occ_line = occ.get("line")
        fields: list[str] = []
        if q.lower() in str(r["name"]).lower():
            fields.append("name")
        if q.lower() in str(r["qualified_name"]).lower():
            fields.append("qualified_name")
        if int(r["m_path"]):
            fields.append("path")
        if used_fts and not fields:
            # Match por token no índice (coluna ``terms`` ou forma
            # normalizada do nome) que o teste de substring não
            # reproduz — declarado, nunca vazio/ambíguo.
            fields.append("terms")
        return DomainSearchHit(
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
            score=_EXACT_SCORE if r["m_exact"] else _DIRECT_SCORE,
            matched_fields=tuple(fields),
            evidence=(f"de:{int(r['entity_id'])}",),
        )

    # --- match direto -------------------------------------------------

    def _direct_rows(
        self,
        req: SearchRequest,
        repository_id: int,
        snapshot_id: int,
        q: str,
    ) -> tuple[
        list[sqlite3.Row], int, bool, SearchDiagnostic | None, bool
    ]:
        """Despacha FTS→LIKE — ``(rows, total, capped, diag, used_fts)``."""
        if self._fts_usable_for(snapshot_id):
            try:
                out = self._direct_rows_fts(
                    req, repository_id, snapshot_id, q)
            except sqlite3.OperationalError as exc:
                return (*self._direct_rows_like(
                    req, repository_id, snapshot_id, q),
                    SearchDiagnostic(
                        code="domain-fts-error", severity="warning",
                        message=f"MATCH de domínio falhou ({exc}) — "
                                "LIKE bounded aplicado"), False)
            if out is not None:
                return *out, None, True
            # query não expressível em MATCH (sem tokens): LIKE cobre.
            return (*self._direct_rows_like(
                req, repository_id, snapshot_id, q), None, False)
        if self._fts_present:
            return (*self._direct_rows_like(
                req, repository_id, snapshot_id, q),
                SearchDiagnostic(
                    code="domain-fts-fallback", severity="info",
                    message="snapshot sem cobertura domain_fts (pré-v12) "
                            "— LIKE bounded aplicado"), False)
        return (*self._direct_rows_like(
            req, repository_id, snapshot_id, q), None, False)

    def _direct_rows_fts(
        self,
        req: SearchRequest,
        repository_id: int,
        snapshot_id: int,
        q: str,
    ) -> tuple[list[sqlite3.Row], int, bool] | None:
        """Candidatos via ``domain_fts`` — ``None`` = MATCH não expressível.

        BM25 (``m_rank``) ordena os candidatos dentro do teto de scan —
        exato primeiro, depois relevância, desempate por identidade. A
        ordem pública final continua a chave determinística do
        docstring de ``search`` (score > identidade): o rank decide só
        quais entidades entram no scan, nunca o contrato do envelope.
        """
        match = _fts_match(q)
        if match is None:
            return None
        like = f"%{_esc_like(q)}%"
        conds: list[str] = []
        params: list[object] = []
        if req.domain:
            conds.append("de.domain = ?")
            params.append(req.domain)
        if req.kind:
            conds.append("de.kind = ?")
            params.append(req.kind)
        if req.path:
            conds.append("f.path LIKE ? ESCAPE '\\'")
            params.append(f"%{_esc_like(req.path)}%")
        extra = (" AND " + " AND ".join(conds)) if conds else ""
        outer = "WHERE de.repository_id = ?" + extra
        # SELECT params (m_path like, m_exact q,q) precedem a subquery
        # (match, snapshot) — ordem textual dos placeholders.
        sel_params = [like, q, q, match, snapshot_id, repository_id, *params]
        list_sql = (
            _FTS_SELECT + outer
            + " GROUP BY de.id"
            " ORDER BY m_exact DESC, m_rank,"
            " de.domain, de.kind, de.qualified_name, de.id LIMIT ?"
        )
        count_sql = (
            "SELECT COUNT(DISTINCT f.entity_id) AS n"
            " FROM (SELECT entity_id FROM domain_fts"
            " WHERE domain_fts MATCH ? AND snapshot_id = ? LIMIT -1) f"
            " JOIN domain_entities de ON de.id = f.entity_id " + outer
        )
        rows = self._conn.execute(
            list_sql, [*sel_params, _CANDIDATE_CAP]).fetchall()
        total = int(self._conn.execute(
            count_sql, [match, snapshot_id, repository_id, *params]
        ).fetchone()["n"])
        return rows, total, total > _CANDIDATE_CAP

    def _direct_rows_like(
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
        # UNION de dois probes indexados em vez de OR entre aliases —
        # o OR cruzava duas tabelas (fe/te) e impedia multi-index OR,
        # forçando scan de domain_relations. Cada braço usa os índices
        # v11 (snapshot_id, from/to_entity_occ_id); UNION já deduplica
        # arestas com ambos os endpoints no conjunto direto.
        edge_rows = self._conn.execute(
            "SELECT r.id AS rel_id, r.type, fe.entity_id AS from_entity,"
            " te.entity_id AS to_entity"
            " FROM domain_relations r"
            " JOIN domain_entity_occurrences fe"
            "   ON fe.id = r.from_entity_occ_id"
            " JOIN domain_entity_occurrences te"
            "   ON te.id = r.to_entity_occ_id"
            " WHERE r.snapshot_id = ?"
            " AND fe.entity_id IN (SELECT value FROM json_each(?))"
            " UNION "
            "SELECT r.id AS rel_id, r.type, fe.entity_id AS from_entity,"
            " te.entity_id AS to_entity"
            " FROM domain_relations r"
            " JOIN domain_entity_occurrences fe"
            "   ON fe.id = r.from_entity_occ_id"
            " JOIN domain_entity_occurrences te"
            "   ON te.id = r.to_entity_occ_id"
            " WHERE r.snapshot_id = ?"
            " AND te.entity_id IN (SELECT value FROM json_each(?))"
            " ORDER BY rel_id LIMIT ?",
            (snapshot_id, ids_json, snapshot_id, ids_json,
             _RELATED_EDGE_CAP),
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
            via[other] = f"via={r['type']}:rel:{int(r['rel_id'])}"
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
