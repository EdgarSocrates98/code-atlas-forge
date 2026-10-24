"""Busca lexical snapshot-scoped via FTS5 ``fts_occurrences`` (lf-0036).

- Rowid do FTS = id da ``symbol_occurrences`` → join direto para
  span/path/símbolo lógico.
- ``snapshot_id`` UNINDEXED garante isolamento: só o snapshot READY
  corrente é consultado.
- Coluna ``terms`` carrega identificadores pré-tokenizados
  (``search/tokenizer.py``) — camelCase/snake_case recuperáveis por
  fragmento.
- Ranking: ``bm25`` nativo do FTS5 com pesos por coluna
  (name > terms > signature > docstring > path).
"""

from __future__ import annotations

import sqlite3

from code_atlas.search.contracts import (
    SearchDiagnostic,
    SearchHit,
    SearchRequest,
)
from code_atlas.search.tokenizer import fts_escape, split_identifier

# Pesos bm25 por coluna indexada (ordem do CREATE VIRTUAL TABLE):
# snapshot_id(unindexed), name, terms, signature, docstring, path,
# language(unindexed), kind(unindexed) → 8 posições.
_BM25_WEIGHTS = "0.0, 10.0, 6.0, 2.0, 1.0, 1.0, 0.0, 0.0"

_MAX_LIMIT = 200


def fts_query(query: str) -> str | None:
    """Monta a expressão MATCH: fragmentos tokenizados OU exato cru.

    - ``foo*`` → prefixo em ``terms``
    - ``pkg.util.helper`` → tokens por segmento (FTS já separa pontos)
    - ``processCustomerOrders`` → exato em ``name`` + fragmentos em
      ``terms``
    """
    q = query.strip()
    if not q:
        return None
    if q.endswith("*") and len(q) > 1:
        stem = q[:-1]
        toks = split_identifier(stem)
        if toks:
            return f"terms : {fts_escape(toks[-1])}*"
        return f"terms : {fts_escape(stem)}*"
    toks = split_identifier(q)
    if not toks:
        return None
    or_terms = " OR ".join(fts_escape(t) for t in toks)
    return (
        f"{{name terms signature docstring}} : ({or_terms})"
        f" OR name : {fts_escape(q)}"
    )


class LexicalSearch:
    """Engine lexical — só lê; erros viram diagnósticos, nunca raise."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def search(
        self,
        req: SearchRequest,
        snapshot_id: int,
        offset: int = 0,
    ) -> tuple[tuple[SearchHit, ...], int, tuple[SearchDiagnostic, ...]]:
        """Retorna (hits, total_estimado, diagnostics)."""
        match = fts_query(req.query)
        if match is None:
            return (), 0, (
                SearchDiagnostic(
                    code="empty-query",
                    severity="warning",
                    message="query vazia após tokenização",
                ),
            )
        limit = max(1, min(req.limit or 20, _MAX_LIMIT))
        where = [
            "fts_occurrences MATCH ?",
            "f.snapshot_id = ?",
        ]
        params: list[object] = [match, snapshot_id]
        if req.language:
            where.append("f.language = ?")
            params.append(req.language)
        if req.kind:
            where.append("f.kind = ?")
            params.append(req.kind)
        if req.path:
            where.append("sf.relative_path LIKE ? ESCAPE '\\'")
            params.append(f"%{_esc_like(req.path)}%")
        base = (
            "FROM fts_occurrences f"
            " JOIN symbol_occurrences o ON o.id = f.rowid"
            " JOIN source_files sf ON sf.id = o.source_file_id"
            " JOIN symbols s ON s.id = o.logical_symbol_id"
        )
        where_sql = " AND ".join(where)
        # SQL composto em variável nomeada — auditoria AST exige que o
        # primeiro arg de execute() seja nome/literal, nunca expressão.
        count_sql = "SELECT COUNT(*) " + base + " WHERE " + where_sql
        select_sql = (
            "SELECT f.rowid AS occ_id, s.qualified_name, f.kind,"
            " f.language, sf.relative_path,"
            " o.span_start, o.span_end, o.line, o.col,"
            f" bm25(fts_occurrences, {_BM25_WEIGHTS}) AS score "
            + base
            + " WHERE "
            + where_sql
            + " ORDER BY score LIMIT ? OFFSET ?"
        )
        try:
            total = int(
                self._conn.execute(count_sql, params).fetchone()[0]
            )
            rows = self._conn.execute(
                select_sql, [*params, limit, offset]
            ).fetchall()
        except sqlite3.OperationalError as exc:
            return (), 0, (
                SearchDiagnostic(
                    code="fts-query-error",
                    severity="error",
                    message=f"MATCH inválida: {exc}",
                ),
            )
        hits = tuple(
            SearchHit(
                occurrence_id=int(r["occ_id"]),
                qualified_name=str(r["qualified_name"]),
                kind=str(r["kind"]),
                language=str(r["language"]),
                path=str(r["relative_path"]),
                span=(
                    {
                        "start_byte": r["span_start"],
                        "end_byte": r["span_end"],
                        "line": r["line"],
                        "col": r["col"],
                    }
                    if r["span_start"] is not None
                    else None
                ),
                score=round(-float(r["score"]), 6),  # bm25 menor = melhor
                strategy="lexical",
                matched_fields=_matched_fields(req.query, r),
                evidence=(f"occ:{int(r['occ_id'])}",),
            )
            for r in rows
        )
        return hits, total, ()


def _matched_fields(query: str, row: sqlite3.Row) -> tuple[str, ...]:
    """Explicação aproximada: quais campos contêm tokens da query.

    Honesta: é um pós-checagem em Python, não o highlight do FTS —
    serve para explicar o hit sem custo de ``highlight()`` por linha.
    """
    toks = set(split_identifier(query))
    if not toks:
        return ()
    fields: list[str] = []
    qname_toks = set(split_identifier(str(row["qualified_name"])))
    if toks & qname_toks:
        fields.append("qualified_name")
    return tuple(fields)


def _esc_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
