"""lf-0134 Cycle 8 Phase 8 — escala federada + correção da cache de view.

Três frentes:

- **cache key da view**: ``view_id`` precisa cobrir membership,
  identidade do repo, pins de snapshot, versões de resolver/adapter e
  ``schema_version``. Os testes adulteram cada componente isoladamente
  e exigem ``view_id`` diferente — e rows da identidade antiga nunca
  vazam para a view nova;
- **stress multi-repo**: workspace de 5 repositórios reais indexados —
  isolamento por snapshot sob query pinada enquanto o repo evolui;
- **view imutável**: pin = leitura ponto-no-tempo. Reindexar o membro
  não corrompe a view: o pin honra o snapshot (que vira STALE de forma
  declarada) e contagens/diagnósticos continuam honestos.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from code_atlas.federation import (
    VIEW_ID_FORMAT,
    Federation,
    compute_view_id,
    shared_key_of,
)
from code_atlas.federation.federation import _analyzer_digest
from code_atlas.search import SearchRequest
from code_atlas.search.federated import FederatedSearchEngine
from code_atlas.storage import connect, migrate, schema_version
from code_atlas.storage.store import Store
from federation.helpers import OPENAPI_SPEC, index_repo, write_repo

Ids = dict[str, object]

N_REPOS = 5


def _repo_files(i: int) -> dict[str, str]:
    """Repo determinístico ``i``: mesmo contrato OpenAPI + mesma
    ``mart.orders`` (shared_key cross-repo) + ``mart.t{i}``/``mart.v{i}``
    exclusivos — o join federado tem o que ligar e o que isolar."""
    return {
        "spec/openapi.yaml": OPENAPI_SPEC,
        "q.sql": (
            "SELECT * FROM mart.orders;\n"
            f"SELECT * FROM mart.t{i};\n"
            f"CREATE VIEW mart.v{i} AS SELECT * FROM mart.orders;\n"
        ),
        "app/mod.py": (
            "def shared_handler():\n"
            f"    return {i}\n"
        ),
    }


@pytest.fixture(scope="module")
def fed5_db(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[sqlite3.Connection, Ids]:
    """5 repos indexados no mesmo banco + workspace com os 5 membros."""
    tmp_path = tmp_path_factory.mktemp("fed5")
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    store = Store(conn)
    fed = Federation(conn)
    ws = fed.create_workspace("scale-five")
    ids: Ids = {"ws": ws.id, "repos": [], "snaps": [], "roots": []}
    for i in range(N_REPOS):
        root = write_repo(tmp_path / f"repo_{i}", _repo_files(i))
        snap = index_repo(conn, root)
        snap_row = store.snapshot_by_id(snap)
        assert snap_row is not None
        fed.add_member(ws, snap_row.repository_id)
        repos = ids["repos"]
        snaps = ids["snaps"]
        roots = ids["roots"]
        assert isinstance(repos, list)
        assert isinstance(snaps, list)
        assert isinstance(roots, list)
        repos.append(snap_row.repository_id)
        snaps.append(snap)
        roots.append(root)
    yield conn, ids
    conn.close()


def _ws(fed: Federation, ids: Ids):
    return fed.get_workspace(int(ids["ws"]))  # type: ignore[arg-type]


def _skey_for(
    conn: sqlite3.Connection, namespace: str, qname: str
) -> str:
    row = conn.execute(
        "SELECT identity_key FROM domain_entities"
        " WHERE namespace = ? AND qualified_name = ? LIMIT 1",
        (namespace, qname),
    ).fetchone()
    assert row is not None, f"entity {namespace}/{qname} não encontrada"
    skey = shared_key_of(str(row["identity_key"]))
    assert skey is not None
    return skey


def _assert_items_match_view(
    items: list[dict[str, object]],
    snap_map: dict[int, int],
) -> None:
    """Todo item sai tagueado com o snapshot resolvido do PRÓPRIO repo —
    nunca uma row de snapshot estranho vaza para a view."""
    for it in items:
        rid = int(it["repository_id"])  # type: ignore[arg-type]
        assert int(it["snapshot_id"]) == snap_map[rid]  # type: ignore[arg-type]


class TestViewCacheKey:
    """Regressão do audit lf-0134: todo componente da cache key invalida."""

    def test_snapshot_pin_change_invalidates_and_never_leaks(
        self, fed5_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed5_db
        fed = Federation(conn)
        ws = _ws(fed, ids)
        repos: list[int] = ids["repos"]  # type: ignore[assignment]
        snaps: list[int] = ids["snaps"]  # type: ignore[assignment]
        root_0 = Path(str(ids["roots"][0]))  # type: ignore[index]

        # repo_0 evolui: mart.extra só existe no snapshot NOVO (S2)
        (root_0 / "q.sql").write_text(
            "SELECT * FROM mart.orders;\nSELECT * FROM mart.t0;\n"
            "SELECT * FROM mart.extra;\n"
            "CREATE VIEW mart.v0 AS SELECT * FROM mart.orders;\n"
        )
        snap_0_new = index_repo(conn, root_0)
        assert snap_0_new != snaps[0]

        skey_extra = _skey_for(conn, "mart", "extra")
        v_old = fed.resolve_view(ws, {repos[0]: snaps[0]})
        v_new = fed.resolve_view(ws, {repos[0]: snap_0_new})
        assert v_old.view_id != v_new.view_id

        # view pinada em S1: a entidade de S2 não existe — e NENHUMA row
        # de S2 vaza (todo item carrega snapshot_id == pin)
        out_old = fed.entity_occurrences(ws, skey_extra, {repos[0]: snaps[0]})
        assert out_old["total"] == 0
        out_new = fed.entity_occurrences(ws, skey_extra, {repos[0]: snap_0_new})
        assert out_new["total"] >= 1
        _assert_items_match_view(
            out_new["items"],  # type: ignore[arg-type]
            v_new.snapshot_map,
        )
        assert all(
            int(i["repository_id"]) != repos[0]  # type: ignore[arg-type]
            or int(i["snapshot_id"]) == snap_0_new  # type: ignore[arg-type]
            for i in out_new["items"]  # type: ignore[union-attr]
        )
        # e a view pinada em S1 continua lendo S1 honestamente
        out_orders = fed.entity_occurrences(
            ws, _skey_for(conn, "mart", "orders"), {repos[0]: snaps[0]}
        )
        _assert_items_match_view(
            out_orders["items"],  # type: ignore[arg-type]
            v_old.snapshot_map,
        )

    def test_membership_change_invalidates_view(
        self, fed5_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed5_db
        fed = Federation(conn)
        ws = _ws(fed, ids)
        repos: list[int] = ids["repos"]  # type: ignore[assignment]
        v_full = fed.resolve_view(ws)
        fed.remove_member(ws, repos[-1])
        try:
            v_minus = fed.resolve_view(ws)
            assert v_minus.view_id != v_full.view_id
            assert repos[-1] not in v_minus.snapshot_map
            # re-add restaura o mesmo mapa → mesma identidade (idempotente)
            fed.add_member(ws, repos[-1])
            v_back = fed.resolve_view(ws)
            assert v_back.view_id == v_full.view_id
        finally:
            fed.add_member(ws, repos[-1])  # idempotente

    def test_repo_identity_rebind_invalidates_view(
        self, fed5_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed5_db
        fed = Federation(conn)
        ws = _ws(fed, ids)
        repos: list[int] = ids["repos"]  # type: ignore[assignment]
        v1 = fed.resolve_view(ws)
        row = conn.execute(
            "SELECT identity_key FROM repositories WHERE id = ?",
            (repos[0],),
        ).fetchone()
        original = str(row["identity_key"])
        # re-bind manual da identity_key (mesmos snapshots, mesmo repo_id)
        with conn:
            conn.execute(
                "UPDATE repositories SET identity_key = ? WHERE id = ?",
                (f"{original}#rebound", repos[0]),
            )
        try:
            v2 = fed.resolve_view(ws)
            assert v2.view_id != v1.view_id
            assert v2.snapshot_map == v1.snapshot_map  # mapa intacto
        finally:
            with conn:
                conn.execute(
                    "UPDATE repositories SET identity_key = ? WHERE id = ?",
                    (original, repos[0]),
                )
        assert fed.resolve_view(ws).view_id == v1.view_id

    def test_analyzer_versions_change_invalidates_view(
        self, fed5_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed5_db
        fed = Federation(conn)
        ws = _ws(fed, ids)
        repos: list[int] = ids["repos"]  # type: ignore[assignment]
        snaps: list[int] = ids["snaps"]  # type: ignore[assignment]
        # pin via override: snaps[0] é o snapshot resolvido do membro
        # (legível mesmo STALE) — o digest adulterado entra na chave
        pins = {repos[0]: snaps[0]}
        v1 = fed.resolve_view(ws, pins)
        row = conn.execute(
            "SELECT analyzer_versions FROM snapshots WHERE id = ?",
            (snaps[0],),
        ).fetchone()
        original = str(row["analyzer_versions"])
        bumped = dict(json.loads(original))
        bumped["domain:sql"] = "9.9.9-tampered"
        with conn:
            conn.execute(
                "UPDATE snapshots SET analyzer_versions = ? WHERE id = ?",
                (json.dumps(bumped, sort_keys=True), snaps[0]),
            )
        try:
            v2 = fed.resolve_view(ws, pins)
            # mesmo snapshot_id, dados com versão diferente → outra view
            assert v2.view_id != v1.view_id
            assert v2.snapshot_map == v1.snapshot_map
        finally:
            with conn:
                conn.execute(
                    "UPDATE snapshots SET analyzer_versions = ? WHERE id = ?",
                    (original, snaps[0]),
                )
        assert fed.resolve_view(ws, pins).view_id == v1.view_id

    def test_schema_version_change_invalidates_view(
        self, fed5_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed5_db
        fed = Federation(conn)
        ws = _ws(fed, ids)
        v1 = fed.resolve_view(ws)
        base = schema_version(conn)
        assert base > 0
        with conn:
            conn.execute(
                "INSERT INTO schema_migrations (version, name, checksum)"
                " VALUES (?, 'tamper-test', 'x')",
                (base + 1,),
            )
        try:
            v2 = fed.resolve_view(ws)
            assert v2.view_id != v1.view_id
        finally:
            with conn:
                conn.execute(
                    "DELETE FROM schema_migrations WHERE version = ?",
                    (base + 1,),
                )
        assert fed.resolve_view(ws).view_id == v1.view_id

    def test_compute_view_id_covers_every_component(self) -> None:
        pairs = {1: 10, 2: 20}
        base = compute_view_id(
            pairs,
            identities={1: "path:/a", 2: "path:/b"},
            member_versions={1: "da", 2: "db"},
            schema=12,
        )
        # mesma resolução → mesma view (ordem de inserção irrelevante)
        assert base == compute_view_id(
            {2: 20, 1: 10},
            identities={2: "path:/b", 1: "path:/a"},
            member_versions={2: "db", 1: "da"},
            schema=12,
        )
        assert base.startswith("fedview:")
        assert VIEW_ID_FORMAT == 2  # formato declarado da cache key
        # cada componente isolado invalida
        assert base != compute_view_id(
            {1: 10, 2: 20, 3: 30},  # membership +
            identities={1: "path:/a", 2: "path:/b", 3: "path:/c"},
            member_versions={1: "da", 2: "db", 3: "dc"},
            schema=12,
        )
        assert base != compute_view_id(
            pairs,
            identities={1: "path:/a-moved", 2: "path:/b"},  # identity
            member_versions={1: "da", 2: "db"},
            schema=12,
        )
        assert base != compute_view_id(
            {1: 11, 2: 20},  # snapshot pin
            identities={1: "path:/a", 2: "path:/b"},
            member_versions={1: "da", 2: "db"},
            schema=12,
        )
        assert base != compute_view_id(
            pairs,
            identities={1: "path:/a", 2: "path:/b"},
            member_versions={1: "da2", 2: "db"},  # adapter/resolver
            schema=12,
        )
        assert base != compute_view_id(
            pairs,
            identities={1: "path:/a", 2: "path:/b"},
            member_versions={1: "da", 2: "db"},
            schema=13,  # schema
        )

    def test_analyzer_digest_declares_absence(self) -> None:
        from code_atlas.contracts.models import Snapshot

        assert _analyzer_digest(None) == ""
        snap = Snapshot(
            repository_id=1, worktree_digest="w",
            analyzer_versions={"domain:sql": "1.0.0"},
        )
        other = Snapshot(
            repository_id=1, worktree_digest="w",
            analyzer_versions={"domain:sql": "1.0.1"},
        )
        assert _analyzer_digest(snap) != _analyzer_digest(other)
        assert len(_analyzer_digest(snap)) == 64


class TestFiveRepoStress:
    """Workspace de 5 membros — isolamento por snapshot sob stress."""

    def test_shared_join_across_all_five(
        self, fed5_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed5_db
        fed = Federation(conn)
        ws = _ws(fed, ids)
        repos: list[int] = ids["repos"]  # type: ignore[assignment]
        view = fed.resolve_view(ws)
        assert len(view.snapshot_map) == N_REPOS
        out = fed.entity_occurrences(
            ws, _skey_for(conn, "mart", "orders"))
        assert set(out["repositories_with_entity"]) == set(repos)  # type: ignore[arg-type]
        _assert_items_match_view(
            out["items"], view.snapshot_map)  # type: ignore[arg-type]

    def test_pinned_member_isolation_under_stress(
        self, fed5_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed5_db
        fed = Federation(conn)
        ws = _ws(fed, ids)
        repos: list[int] = ids["repos"]  # type: ignore[assignment]
        snaps: list[int] = ids["snaps"]  # type: ignore[assignment]
        root_1 = Path(str(ids["roots"][1]))  # type: ignore[index]

        # repo_1 evolui — mart.r1_new só no snapshot novo
        (root_1 / "new.sql").write_text("SELECT * FROM mart.r1_new;\n")
        snap_1_new = index_repo(conn, root_1)

        # query pinada a S1 enquanto S2 já é o READY: nunca retorna S2
        view = fed.resolve_view(ws, {repos[1]: snaps[1]})
        assert view.snapshot_map[repos[1]] == snaps[1]
        skey_new = _skey_for(conn, "mart", "r1_new")
        out = fed.entity_occurrences(ws, skey_new, {repos[1]: snaps[1]})
        assert out["total"] == 0
        # nem busca federada sob o pin vê rows de S2
        res = FederatedSearchEngine(conn).search(
            SearchRequest(query="r1_new"), ws.id,
            pins={repos[1]: snaps[1]},
        )
        assert not any(
            h.snapshot_id == snap_1_new and h.repository_id == repos[1]
            for h in res.domain_items
        )
        # soltar o pin → S2 aparece, só no repo_1, só com snap_1_new
        res2 = FederatedSearchEngine(conn).search(
            SearchRequest(query="r1_new"), ws.id)
        hits = [h for h in res2.domain_items
                if h.qualified_name == "r1_new"]
        assert hits
        assert {h.repository_id for h in hits} == {repos[1]}
        assert {h.snapshot_id for h in hits} == {snap_1_new}
        assert res2.view.view_id != res.view.view_id

    def test_graph_stress_snapshot_isolation(
        self, fed5_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed5_db
        fed = Federation(conn)
        ws = _ws(fed, ids)
        g = fed.graph(ws, depth=2, limit=5000)
        assert g.stats["member_repositories"] == N_REPOS
        # nós sempre ancorados no snapshot do próprio membro
        for n in g.nodes:
            assert n.snapshot_id == g.view.snapshot_map[n.repository_id]
        cross = [e for e in g.edges if e.from_repo != e.to_repo]
        assert cross  # mart.orders + endpoint + service ligam os 5
        assert all(e.kind == "shared_entity" and e.computed for e in cross)
        # entidades exclusivas nunca ligam cross-repo
        shared_keys = {n.shared_key for n in g.nodes}
        for i in range(N_REPOS):
            unique_keys = [
                s for s in shared_keys if f"/mart/t{i}" in s
            ]
            for skey in unique_keys:
                nodes_i = [n for n in g.nodes if n.shared_key == skey]
                assert {n.repository_id for n in nodes_i} <= {
                    ids["repos"][i]  # type: ignore[index]
                }

    def test_search_merge_five_members_deterministic(
        self, fed5_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed5_db
        ws_id = int(ids["ws"])  # type: ignore[arg-type]
        eng = FederatedSearchEngine(conn)
        a = eng.search(SearchRequest(query="orders"), ws_id)
        b = eng.search(SearchRequest(query="orders"), ws_id)
        assert a.view.view_id == b.view.view_id
        assert a.members_searched == N_REPOS
        key = lambda h: (h.repository_id, h.snapshot_id,  # noqa: E731
                         h.entity_id)
        assert [key(h) for h in a.domain_items] == \
            [key(h) for h in b.domain_items]
        # narrowing a 1 membro: só ele é pesquisado, view segue 5-wide
        one = eng.search(
            SearchRequest(query="orders"), ws_id,
            repository_id=int(ids["repos"][2]),  # type: ignore[index]
        )
        assert one.members_searched == 1
        assert one.view.view_id == a.view.view_id
        assert {h.repository_id for h in one.items} <= {
            int(ids["repos"][2])  # type: ignore[index]
        }


class TestImmutableView:
    """Pin = leitura ponto-no-tempo; mutação do membro não corrompe."""

    def test_pin_freezes_view_identity_across_reindex(
        self, fed5_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed5_db
        fed = Federation(conn)
        ws = _ws(fed, ids)
        repos: list[int] = ids["repos"]  # type: ignore[assignment]
        snaps: list[int] = ids["snaps"]  # type: ignore[assignment]
        root_2 = Path(str(ids["roots"][2]))  # type: ignore[index]

        fed.pin_member(ws, repos[2], snaps[2])
        v1 = fed.resolve_view(ws)
        (root_2 / "grow.sql").write_text("SELECT * FROM mart.r2_grow;\n")
        snap_2_new = index_repo(conn, root_2)
        assert snap_2_new != snaps[2]

        v2 = fed.resolve_view(ws)
        # pin congela a identidade — mesmo mapa, mesmo view_id
        assert v2.view_id == v1.view_id
        assert v2.snapshot_map[repos[2]] == snaps[2]
        member = next(
            m for m in v2.members if m.repository_id == repos[2])
        # envelhecimento declarado: S1 virou STALE, o pin continua honrado
        assert member.resolution == "pinned"
        assert member.snapshot_status == "STALE"

        # a view pinada continua lendo S1 — r2_grow invisível, nada vaza
        out = fed.entity_occurrences(
            ws, _skey_for(conn, "mart", "r2_grow"))
        assert out["total"] == 0
        out_orders = fed.entity_occurrences(
            ws, _skey_for(conn, "mart", "orders"))
        _assert_items_match_view(
            out_orders["items"], v2.snapshot_map)  # type: ignore[arg-type]

        # unpin → membro flutua para o READY novo e a identidade muda
        fed.unpin_member(ws, repos[2])
        v3 = fed.resolve_view(ws)
        assert v3.view_id != v2.view_id
        assert v3.snapshot_map[repos[2]] == snap_2_new

    def test_unreadable_member_declared_not_fabricated(
        self, fed5_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed5_db
        fed = Federation(conn)
        ws = _ws(fed, ids)
        repos: list[int] = ids["repos"]  # type: ignore[assignment]
        snaps: list[int] = ids["snaps"]  # type: ignore[assignment]
        # membro sem snapshot legível entra na view como ausência —
        # contagens cobrem só os membros legíveis, diagnosticado na busca
        with conn:
            conn.execute(
                "UPDATE snapshots SET status = 'FAILED' WHERE id = ?",
                (snaps[3],),
            )
        try:
            view = fed.resolve_view(ws)
            member = next(
                m for m in view.members if m.repository_id == repos[3])
            assert member.snapshot_id is None
            assert member.resolution == "no-ready-snapshot"
            assert repos[3] not in view.snapshot_map
            res = FederatedSearchEngine(conn).search(
                SearchRequest(query="orders"), int(ids["ws"]))  # type: ignore[arg-type]
            assert res.members_searched == N_REPOS - 1
            assert any(
                d.code == "member-no-snapshot" for d in res.diagnostics)
            # nenhum hit tagueado com o repo ilegível
            assert not any(
                h.repository_id == repos[3] for h in res.items)
            assert not any(
                h.repository_id == repos[3] for h in res.domain_items)
        finally:
            with conn:
                conn.execute(
                    "UPDATE snapshots SET status = 'READY' WHERE id = ?",
                    (snaps[3],),
                )

    def test_graph_counts_honest_with_pinned_mutation(
        self, fed5_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed5_db
        fed = Federation(conn)
        ws = _ws(fed, ids)
        repos: list[int] = ids["repos"]  # type: ignore[assignment]
        snaps: list[int] = ids["snaps"]  # type: ignore[assignment]

        # pin de repo_4 em S1 + repo_4 evolui — o grafo continua honesto:
        # stats contam só o que a view realmente leu
        fed.pin_member(ws, repos[4], snaps[4])
        root_4 = Path(str(ids["roots"][4]))  # type: ignore[index]
        (root_4 / "grow.sql").write_text("SELECT * FROM mart.r4_grow;\n")
        index_repo(conn, root_4)

        g = fed.graph(ws, depth=3, limit=5000)
        assert g.view.snapshot_map[repos[4]] == snaps[4]
        assert g.stats["member_repositories"] == N_REPOS
        assert g.stats["intra_edges"] >= 0
        assert g.stats["shared_edges"] >= 0
        assert g.stats["candidate_entities"] >= len(g.nodes)
        qnames = {n.qualified_name for n in g.nodes}
        assert "r4_grow" not in qnames  # só no snapshot novo, pinado fora
        for n in g.nodes:
            assert n.snapshot_id == g.view.snapshot_map[n.repository_id]
