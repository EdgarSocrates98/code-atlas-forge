"""Busca federada multi-repo (lf-0119, Cycle 7 Phase 11).

Resolve a view do workspace via ``Federation.resolve_view`` (override
de pin do request → pin persistido → READY mais recente) e executa a
MESMA busca em cada escopo ``(repo, snapshot)`` através de
``SearchEngine.search_scoped`` — nenhum snapshot é misturado: cada
leitura fica ancorada no ``snapshot_id`` resolvido daquele membro, e
pins STALE são honrados (o contrato de ``search_scoped`` não revalida
status; a view declara ``resolution``/``snapshot_status`` por membro).

Merge determinístico e NUNCA colapsante:

- a ordenação é posicional — ``(rank dentro do membro, repository_id,
  occurrence_id)`` — porque scores de backends distintos não são
  comparáveis entre repositórios (bm25 é por corpus); rank dentro do
  próprio escopo é a única ordem honesta;
- hits homônimos em repos distintos NUNCA colapsam — nenhum dedup é
  aplicado além do já feito por membro, e todo item sai tagueado com
  ``repository_id`` + ``snapshot_id`` do escopo que o produziu;
- a faceta de domínio (``domain_items``) é fundida pela mesma regra e
  permanece separada dos hits de símbolo.

``view_id`` entra no fingerprint do cursor: reindexar ou repinar um
membro muda a identidade da view e cursores antigos são rejeitados —
a mesma invariância cross-revision do engine single-repo (lf-0058).
"""

from __future__ import annotations

import dataclasses
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass

from code_atlas.federation import FederatedView, Federation
from code_atlas.search.contracts import (
    SEARCH_SCHEMA_VERSION,
    DomainSearchHit,
    SearchDiagnostic,
    SearchHit,
    SearchRequest,
    decode_cursor,
    encode_cursor,
    query_fingerprint,
)
from code_atlas.search.engine import SearchEngine
from code_atlas.storage.store import Store

_MAX_LIMIT = 200
# Teto do merge por membro — janela de candidatos por escopo, nunca
# ilimitada (análogo ao fetch interno do hybrid).
_MEMBER_FETCH_CAP = 200


@dataclass(frozen=True)
class FederatedSearchResult:
    """Envelope da busca federada — view + items tagueados por escopo."""

    view: FederatedView
    query: str
    strategy: str
    ranking_version: str
    items: tuple[SearchHit, ...]
    domain_items: tuple[DomainSearchHit, ...]
    domain_total: int
    members_searched: int
    repository_filter: int | None
    limit: int
    truncated: bool
    next_cursor: str | None
    diagnostics: tuple[SearchDiagnostic, ...]

    def to_dict(self) -> dict[str, object]:
        out = self.view.to_dict()  # workspace_id, workspace, view_id, members
        out.update({
            "schema_version": SEARCH_SCHEMA_VERSION,
            "query": self.query,
            "strategy": self.strategy,
            "ranking_version": self.ranking_version,
            "items": [dataclasses.asdict(h) for h in self.items],
            "domain_items": [dataclasses.asdict(h)
                             for h in self.domain_items],
            "domain_total": self.domain_total,
            "members_searched": self.members_searched,
            "repository_filter": self.repository_filter,
            "repositories_with_hits": sorted({
                h.repository_id for h in self.items
                if h.repository_id is not None}),
            "limit": self.limit,
            "truncated": self.truncated,
            "next_cursor": self.next_cursor,
            "diagnostics": [dataclasses.asdict(d)
                            for d in self.diagnostics],
        })
        return out


class FederatedSearchEngine:
    """Busca cross-repo sobre a view de um workspace — somente leitura."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._fed = Federation(conn)
        self._engine = SearchEngine(conn)
        self._store = Store(conn)

    def search(
        self,
        req: SearchRequest,
        workspace: str | int,
        pins: Mapping[int, int] | None = None,
        *,
        repository_id: int | None = None,
    ) -> FederatedSearchResult:
        """Busca ``req`` em cada membro da view do workspace.

        ``workspace``: id (int/dígito) ou nome — ``get_workspace``.
        ``pins``: overrides ``{repository_id: snapshot_id}`` da view.
        ``req.snapshot_id`` é açúcar para pin do repo dono do snapshot
        (o override explícito prevalece sobre pins persistidos; repo
        fora do workspace → ``InvalidPinError`` tipado).
        ``repository_id``: narrowing honesto — só aquele membro é
        pesquisado; a view (e sua identidade) cobre o workspace inteiro.
        """
        eff_pins = dict(pins or {})
        if req.snapshot_id is not None:
            from code_atlas.federation.model import (
                SnapshotNotFoundError,
            )
            snap = self._store.snapshot_by_id(req.snapshot_id)
            if snap is None:
                raise SnapshotNotFoundError(
                    f"snapshot {req.snapshot_id} not found")
            eff_pins[snap.repository_id] = snap.id or 0
        ws = self._fed.get_workspace(workspace)
        view = self._fed.resolve_view(ws, eff_pins)

        lim = max(1, min(req.limit or 20, _MAX_LIMIT))
        fp = query_fingerprint(req.query, {
            "language": req.language, "kind": req.kind,
            "path": req.path, "domain": req.domain,
            "repository": ("" if repository_id is None
                           else str(repository_id)),
            "view": view.view_id,
        })
        offset = 0
        diags: list[SearchDiagnostic] = []
        if req.cursor:
            decoded = decode_cursor(req.cursor, fp)
            if decoded is None:
                diags.append(SearchDiagnostic(
                    code="invalid-cursor", severity="error",
                    message="cursor não corresponde a esta query/view"))
                return self._result(req, view, (), 0, repository_id,
                                    lim, False, None, diags)
            offset = decoded

        fetch = min(offset + lim, _MEMBER_FETCH_CAP)
        (member_hits, member_domain, domain_total, searched,
         truncated, member_ranking) = self._member_results(
            req, view, fetch, repository_id, diags)

        member_hits.sort(key=lambda t: (t[0], t[1], t[2].occurrence_id))
        member_domain.sort(key=lambda t: (t[0], t[1], t[2].entity_id))
        merged = [h for _pos, _rid, h in member_hits]
        page = tuple(merged[offset:offset + lim])
        domain_page = tuple(
            h for _pos, _rid, h in member_domain)[:lim]
        truncated = truncated or (offset + len(page) < len(merged))
        cursor = (encode_cursor(offset + len(page), fp)
                  if truncated else None)
        if repository_id is not None and searched == 0:
            diags.append(SearchDiagnostic(
                code="member-filter-empty", severity="warning",
                message=f"repository_id {repository_id} não é membro "
                        f"pesquisável desta view"))
        return self._result(req, view, page, domain_total, repository_id,
                            lim, truncated, cursor, diags,
                            domain_items=domain_page,
                            members_searched=searched,
                            member_ranking=member_ranking)

    def _member_results(
        self,
        req: SearchRequest,
        view: FederatedView,
        fetch: int,
        repository_id: int | None,
        diags: list[SearchDiagnostic],
    ) -> tuple[
        list[tuple[int, int, SearchHit]],
        list[tuple[int, int, DomainSearchHit]],
        int, int, bool, str,
    ]:
        """Executa a busca em cada membro resolvido da view.

        Retorna ``(hits, domain_hits, domain_total, searched,
        truncated, member_ranking)`` — hits como ``(rank_no_membro,
        repository_id, hit)`` para o merge posicional. Diagnósticos de
        membros são propagados com prefixo de escopo na mensagem.
        """
        roots = self._repo_roots()
        member_hits: list[tuple[int, int, SearchHit]] = []
        member_domain: list[tuple[int, int, DomainSearchHit]] = []
        domain_total = 0
        searched = 0
        truncated = False
        member_ranking = ""
        for m in view.members:
            if m.snapshot_id is None:
                if repository_id in (None, m.repository_id):
                    diags.append(SearchDiagnostic(
                        code="member-no-snapshot", severity="warning",
                        message=f"repo:{m.repository_id} sem snapshot "
                                f"legível ({m.resolution})"))
                continue
            if repository_id is not None and m.repository_id != repository_id:
                continue
            searched += 1
            mreq = dataclasses.replace(
                req, repository_root=roots.get(m.repository_id, ""),
                snapshot_id=m.snapshot_id, cursor=None, limit=fetch)
            res = self._engine.search_scoped(
                mreq, m.repository_id, m.snapshot_id)
            member_ranking = member_ranking or res.ranking_version
            diags.extend(
                SearchDiagnostic(
                    code=d.code, severity=d.severity,
                    message=f"repo:{m.repository_id} "
                            f"snap:{m.snapshot_id} — {d.message}")
                for d in res.diagnostics)
            for pos, h in enumerate(res.items):
                member_hits.append((pos, m.repository_id, h))
            for pos, dh in enumerate(res.domain_items):
                member_domain.append((pos, m.repository_id, dh))
            domain_total += res.domain_total
            truncated = truncated or res.truncated
        return (member_hits, member_domain, domain_total, searched,
                truncated, member_ranking)

    def _repo_roots(self) -> dict[int, str]:
        rows = self._conn.execute(
            "SELECT id, root_handle FROM repositories").fetchall()
        return {int(r["id"]): str(r["root_handle"]) for r in rows}

    def _result(
        self,
        req: SearchRequest,
        view: FederatedView,
        items: tuple[SearchHit, ...],
        domain_total: int,
        repository_filter: int | None,
        limit: int,
        truncated: bool,
        cursor: str | None,
        diags: list[SearchDiagnostic],
        *,
        domain_items: tuple[DomainSearchHit, ...] = (),
        members_searched: int = 0,
        member_ranking: str = "",
    ) -> FederatedSearchResult:
        # versão do merge declarada + versão do ranking intra-membro —
        # scores nunca são comparados cross-repo, só ranks locais.
        ranking = "federated-rank-v1"
        if member_ranking:
            ranking = f"{ranking}+{member_ranking}"
        return FederatedSearchResult(
            view=view, query=req.query, strategy=req.strategy,
            ranking_version=ranking,
            items=items, domain_items=domain_items,
            domain_total=domain_total,
            members_searched=members_searched,
            repository_filter=repository_filter,
            limit=limit, truncated=truncated, next_cursor=cursor,
            diagnostics=tuple(diags),
        )
