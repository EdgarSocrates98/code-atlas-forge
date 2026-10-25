"""lf-0118 Phase 10 — engine e store de federação multi-repo.

Cobre o critério central: federação sobre repositórios já indexados
**sem quebrar isolamento de snapshot** — cada leitura é ancorada no
``snapshot_id`` resolvido daquele membro; o join cross-repo é a
``shared_key`` (identidade lógica menos o repo); arestas
``shared_entity`` são computadas, nunca persistidas; nenhuma relação
cross-repo é inventada.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from code_atlas.federation import (
    Federation,
    InvalidKeyError,
    InvalidPinError,
    MemberNotFoundError,
    RepositoryNotFoundError,
    SnapshotNotFoundError,
    SnapshotNotReadyError,
    WorkspaceExistsError,
    WorkspaceNotFoundError,
    WorkspaceSizeError,
    normalize_lookup_key,
    shared_key_of,
)
from code_atlas.federation.federation import FEDERATION_RESOLVER_ID
from federation.helpers import index_repo

Ids = dict[str, object]


def _fed(conn: sqlite3.Connection) -> Federation:
    return Federation(conn)


def _workspace(fed: Federation, ids: Ids) -> int:
    ws = fed.create_workspace("orders-fed")
    fed.add_member(ws, int(ids["repo_a"]))  # type: ignore[arg-type]
    fed.add_member(ws, int(ids["repo_b"]))  # type: ignore[arg-type]
    return ws.id


def _shared_key_for(
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


class TestWorkspaceCrud:
    def test_create_and_list(
        self, fed_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, _ = fed_db
        fed = _fed(conn)
        ws = fed.create_workspace("alpha")
        assert ws.id > 0 and ws.name == "alpha"
        assert [w.name for w in fed.list_workspaces()] == ["alpha"]

    def test_duplicate_name_rejected(
        self, fed_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, _ = fed_db
        fed = _fed(conn)
        fed.create_workspace("dup")
        with pytest.raises(WorkspaceExistsError):
            fed.create_workspace("dup")

    def test_workspace_not_found(
        self, fed_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, _ = fed_db
        with pytest.raises(WorkspaceNotFoundError):
            _fed(conn).get_workspace("nope")
        with pytest.raises(WorkspaceNotFoundError):
            _fed(conn).get_workspace(999)


class TestMembers:
    def test_add_only_indexed_repositories(
        self, fed_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed_db
        fed = _fed(conn)
        ws = fed.create_workspace("w")
        with pytest.raises(RepositoryNotFoundError):
            fed.add_member(ws, 424242)  # nunca indexado
        member = fed.add_member(ws, int(ids["repo_a"]))  # type: ignore[arg-type]
        assert member.repository_id == int(ids["repo_a"])  # type: ignore[arg-type]
        # idempotente (INSERT OR IGNORE + UNIQUE)
        again = fed.add_member(ws, int(ids["repo_a"]))  # type: ignore[arg-type]
        assert again.id == member.id
        assert fed._fstore.member_count(ws.id) == 1

    def test_resolve_repository_by_path_id_and_handle(
        self, fed_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed_db
        fed = _fed(conn)
        repo_a = int(ids["repo_a"])  # type: ignore[arg-type]
        assert fed.resolve_repository(str(repo_a)) == repo_a
        assert (
            fed.resolve_repository(str(ids["root_a"])) == repo_a
        )
        row = conn.execute(
            "SELECT identity_key FROM repositories WHERE id = ?",
            (repo_a,),
        ).fetchone()
        assert fed.resolve_repository(str(row["identity_key"])) == repo_a
        with pytest.raises(RepositoryNotFoundError):
            fed.resolve_repository("/definitely/not/indexed")
        with pytest.raises(RepositoryNotFoundError):
            fed.resolve_repository("777777")

    def test_workspace_requires_two_members(
        self, fed_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed_db
        fed = _fed(conn)
        ws = fed.create_workspace("thin")
        fed.add_member(ws, int(ids["repo_a"]))  # type: ignore[arg-type]
        with pytest.raises(WorkspaceSizeError):
            fed.resolve_view(ws)


class TestPinning:
    def test_pin_member_and_unpin(
        self, fed_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed_db
        fed = _fed(conn)
        ws = fed.get_workspace(_workspace(fed, ids))
        m = fed.pin_member(ws, int(ids["repo_a"]), int(ids["snap_a"]))  # type: ignore[arg-type]
        assert m.pinned_snapshot_id == int(ids["snap_a"])  # type: ignore[arg-type]
        m2 = fed.unpin_member(ws, int(ids["repo_a"]))  # type: ignore[arg-type]
        assert m2.pinned_snapshot_id is None

    def test_pin_rejects_foreign_or_missing_snapshot(
        self, fed_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed_db
        fed = _fed(conn)
        ws = fed.get_workspace(_workspace(fed, ids))
        with pytest.raises(SnapshotNotFoundError):
            # snapshot do repo B não pode pinnar o repo A
            fed.pin_member(ws, int(ids["repo_a"]), int(ids["snap_b"]))  # type: ignore[arg-type]
        with pytest.raises(SnapshotNotFoundError):
            fed.pin_member(ws, int(ids["repo_a"]), 999999)  # type: ignore[arg-type]

    def test_pin_requires_membership_and_readable_status(
        self, fed_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed_db
        fed = _fed(conn)
        ws = fed.create_workspace("w")
        with pytest.raises(MemberNotFoundError):
            fed.pin_member(ws, int(ids["repo_a"]), int(ids["snap_a"]))  # type: ignore[arg-type]
        _workspace(fed, ids)
        ws2 = fed.get_workspace("orders-fed")
        conn.execute(
            "UPDATE snapshots SET status='FAILED' WHERE id = ?",
            (int(ids["snap_b"]),),  # type: ignore[arg-type]
        )
        conn.commit()
        with pytest.raises(SnapshotNotReadyError):
            fed.pin_member(ws2, int(ids["repo_b"]), int(ids["snap_b"]))  # type: ignore[arg-type]


class TestView:
    def test_view_resolves_latest_ready_by_default(
        self, fed_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed_db
        fed = _fed(conn)
        ws = fed.get_workspace(_workspace(fed, ids))
        view = fed.resolve_view(ws)
        assert view.snapshot_map == {
            int(ids["repo_a"]): int(ids["snap_a"]),  # type: ignore[arg-type]
            int(ids["repo_b"]): int(ids["snap_b"]),  # type: ignore[arg-type]
        }
        assert all(m.resolution == "latest-ready" for m in view.members)
        assert view.view_id.startswith("fedview:")
        # snapshot_map está gravado no payload — ausência nunca presumida
        payload = view.to_dict()
        assert {m["repository_id"] for m in payload["members"]} == {
            int(ids["repo_a"]), int(ids["repo_b"])  # type: ignore[arg-type]
        }

    def test_view_identity_changes_with_pin(
        self, fed_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed_db
        fed = _fed(conn)
        ws = fed.get_workspace(_workspace(fed, ids))
        v1 = fed.resolve_view(ws)
        fed.pin_member(ws, int(ids["repo_a"]), int(ids["snap_a"]))  # type: ignore[arg-type]
        v2 = fed.resolve_view(ws)
        assert v1.view_id == v2.view_id  # mesmo snapshot → mesma view
        # override de pin para snapshot diferente do membro
        root_a = Path(str(ids["root_a"]))
        (root_a / "q.sql").write_text(
            "SELECT * FROM mart.orders;\nSELECT * FROM mart.extra;\n"
        )
        snap_a2 = index_repo(conn, root_a)
        v3 = fed.resolve_view(ws, {int(ids["repo_a"]): snap_a2})  # type: ignore[arg-type]
        assert v3.view_id != v2.view_id
        assert v3.snapshot_map[int(ids["repo_a"])] == snap_a2  # type: ignore[arg-type]
        assert v3.snapshot_map[int(ids["repo_b"])] == int(ids["snap_b"])  # type: ignore[arg-type]

    def test_pin_override_isolates_other_members(
        self, fed_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed_db
        fed = _fed(conn)
        ws = fed.get_workspace(_workspace(fed, ids))
        v = fed.resolve_view(ws, {int(ids["repo_a"]): int(ids["snap_a"])})  # type: ignore[arg-type]
        assert v.snapshot_map[int(ids["repo_b"])] == int(ids["snap_b"])  # type: ignore[arg-type]

    def test_pin_override_validates_membership(
        self, fed_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed_db
        fed = _fed(conn)
        ws = fed.get_workspace(_workspace(fed, ids))
        with pytest.raises(InvalidPinError):
            fed.resolve_view(ws, {999999: int(ids["snap_a"])})  # type: ignore[arg-type]

    def test_new_ready_snapshot_is_picked_when_unpinned(
        self, fed_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed_db
        fed = _fed(conn)
        ws = fed.get_workspace(_workspace(fed, ids))
        v1 = fed.resolve_view(ws)
        root_a = Path(str(ids["root_a"]))
        (root_a / "extra.sql").write_text("SELECT * FROM mart.extra;\n")
        snap_a2 = index_repo(conn, root_a)
        v2 = fed.resolve_view(ws)
        assert v2.snapshot_map[int(ids["repo_a"])] == snap_a2  # type: ignore[arg-type]
        assert v2.view_id != v1.view_id


class TestEntityLookup:
    def test_shared_entity_across_repositories(
        self, fed_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed_db
        fed = _fed(conn)
        ws = fed.get_workspace(_workspace(fed, ids))
        skey = _shared_key_for(conn, "mart", "orders")
        out = fed.entity_occurrences(ws, skey)
        assert out["total"] >= 2
        assert set(out["repositories_with_entity"]) == {
            int(ids["repo_a"]), int(ids["repo_b"])  # type: ignore[arg-type]
        }
        for item in out["items"]:
            assert item["namespace"] == "mart"
            assert item["qualified_name"] == "orders"
            assert item["snapshot_id"] == {
                int(ids["repo_a"]): int(ids["snap_a"]),  # type: ignore[arg-type]
                int(ids["repo_b"]): int(ids["snap_b"]),  # type: ignore[arg-type]
            }[item["repository_id"]]

    def test_lookup_accepts_full_identity_key(
        self, fed_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed_db
        fed = _fed(conn)
        ws = fed.get_workspace(_workspace(fed, ids))
        row = conn.execute(
            "SELECT identity_key FROM domain_entities"
            " WHERE namespace = 'mart' AND qualified_name = 'orders'"
            " LIMIT 1"
        ).fetchone()
        out = fed.entity_occurrences(ws, str(row["identity_key"]))
        assert out["total"] >= 2

    def test_same_name_different_namespace_never_matches(
        self, fed_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed_db
        fed = _fed(conn)
        ws = fed.get_workspace(_workspace(fed, ids))
        skey = _shared_key_for(conn, "mart", "orders")
        out = fed.entity_occurrences(ws, skey)
        assert all(i["namespace"] == "mart" for i in out["items"])
        # other.orders só existe no repo B e nunca casa com mart.orders
        skey_other = _shared_key_for(conn, "other", "orders")
        out2 = fed.entity_occurrences(ws, skey_other)
        assert out2["repositories_with_entity"] == [int(ids["repo_b"])]  # type: ignore[arg-type]
        assert all(i["namespace"] == "other" for i in out2["items"])

    def test_bad_key_rejected_never_substring_matched(
        self, fed_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed_db
        fed = _fed(conn)
        ws = fed.get_workspace(_workspace(fed, ids))
        for bad in ("orders", "mart.orders", "dom:sql/too/short",
                    "", "notakey"):
            with pytest.raises(InvalidKeyError):
                fed.entity_occurrences(ws, bad)

    def test_shared_openapi_endpoint(
        self, fed_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed_db
        fed = _fed(conn)
        ws = fed.get_workspace(_workspace(fed, ids))
        row = conn.execute(
            "SELECT identity_key FROM domain_entities"
            " WHERE domain = 'api' AND kind = 'api_endpoint' LIMIT 1"
        ).fetchone()
        assert row is not None
        out = fed.entity_occurrences(ws, str(row["identity_key"]))
        assert out["total"] >= 2
        assert set(out["repositories_with_entity"]) == {
            int(ids["repo_a"]), int(ids["repo_b"])  # type: ignore[arg-type]
        }
        assert all(
            "orders" in i["qualified_name"] for i in out["items"]
        )


class TestSnapshotIsolation:
    def test_pinned_view_ignores_newer_snapshot(
        self, fed_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed_db
        fed = _fed(conn)
        ws = fed.get_workspace(_workspace(fed, ids))
        fed.pin_member(ws, int(ids["repo_a"]), int(ids["snap_a"]))  # type: ignore[arg-type]
        # repo A evolui — mart.extra só existe no snapshot NOVO
        root_a = Path(str(ids["root_a"]))
        (root_a / "q.sql").write_text("SELECT * FROM mart.extra;\n")
        snap_a2 = index_repo(conn, root_a)
        skey_extra = _shared_key_for(conn, "mart", "extra")
        out = fed.entity_occurrences(ws, skey_extra)
        assert out["total"] == 0  # pin congela — extra invisível
        # despin → view enxerga o READY novo; B intocado
        fed.unpin_member(ws, int(ids["repo_a"]))  # type: ignore[arg-type]
        out2 = fed.entity_occurrences(ws, skey_extra)
        assert out2["repositories_with_entity"] == [int(ids["repo_a"])]  # type: ignore[arg-type]
        assert all(i["snapshot_id"] == snap_a2 for i in out2["items"])

    def test_member_b_isolation_when_a_changes(
        self, fed_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed_db
        fed = _fed(conn)
        ws = fed.get_workspace(_workspace(fed, ids))
        root_a = Path(str(ids["root_a"]))
        (root_a / "q.sql").write_text("SELECT * FROM mart.extra;\n")
        index_repo(conn, root_a)
        v = fed.resolve_view(ws)
        # B segue no snapshot original — nada de "drift" implícito
        assert v.snapshot_map[int(ids["repo_b"])] == int(ids["snap_b"])  # type: ignore[arg-type]
        m_b = next(
            m for m in v.members
            if m.repository_id == int(ids["repo_b"])  # type: ignore[arg-type]
        )
        assert m_b.resolution == "latest-ready"


class TestGraph:
    def test_shared_edges_computed_not_persisted(
        self, fed_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed_db
        fed = _fed(conn)
        ws = fed.get_workspace(_workspace(fed, ids))
        g = fed.graph(ws, depth=0)
        shared = [e for e in g.edges if e.kind == "shared_entity"]
        assert shared  # mart.orders + endpoint + service compartilhados
        assert all(e.computed for e in shared)
        assert all(e.resolver == FEDERATION_RESOLVER_ID for e in shared)
        assert all(e.relation_id is None for e in shared)
        assert all(e.from_repo != e.to_repo for e in shared)
        # nada escrito em domain_relations pelo grafo
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM domain_relations"
            " WHERE resolver = ?", (FEDERATION_RESOLVER_ID,)
        ).fetchone()
        assert row["c"] == 0

    def test_depth_zero_returns_seeds_only(
        self, fed_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed_db
        fed = _fed(conn)
        ws = fed.get_workspace(_workspace(fed, ids))
        g = fed.graph(ws, depth=0)
        repos = {n.repository_id for n in g.nodes}
        assert repos == {int(ids["repo_a"]), int(ids["repo_b"])}  # type: ignore[arg-type]
        qnames = {n.qualified_name for n in g.nodes}
        assert "orders" in qnames  # mart.orders nos dois repos
        assert g.seeds == len(g.nodes)
        # intra edges existem mas não estavam no raio depth=0?
        # depth=0 → nós = sementes; arestas entre sementes aparecem
        assert g.truncated is False

    def test_bounded_depth_expands_intra_snapshot(
        self, fed_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed_db
        fed = _fed(conn)
        ws = fed.get_workspace(_workspace(fed, ids))
        g0 = fed.graph(ws, depth=0)
        g1 = fed.graph(ws, depth=1)
        assert len(g1.nodes) >= len(g0.nodes)
        qnames1 = {n.qualified_name for n in g1.nodes}
        # mart.v_orders (view em A) é vizinho intra de mart.orders
        assert "v_orders" in qnames1 or len(g1.edges) > len(g0.edges)

    def test_depth_is_capped(
        self, fed_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed_db
        fed = _fed(conn)
        ws = fed.get_workspace(_workspace(fed, ids))
        g = fed.graph(ws, depth=999)
        assert g.depth == 8  # MAX_GRAPH_DEPTH

    def test_no_fabricated_cross_repo_relations(
        self, fed_db: tuple[sqlite3.Connection, Ids]
    ) -> None:
        conn, ids = fed_db
        fed = _fed(conn)
        ws = fed.get_workspace(_workspace(fed, ids))
        g = fed.graph(ws, depth=8, limit=5000)
        cross = [
            e for e in g.edges if e.from_repo != e.to_repo
        ]
        # o único vínculo cross-repo permitido é shared_entity computado
        assert all(e.kind == "shared_entity" and e.computed for e in cross)
        # nós sempre carregam o snapshot do próprio membro
        for n in g.nodes:
            assert n.snapshot_id == g.view.snapshot_map[n.repository_id]


class TestKeyHelpers:
    def test_shared_key_of_strips_repository_segment(self) -> None:
        key = "dom:sql/path%3A%2Ftmp%2Frepo/%2Froot/cat/ns/env/t.orders"
        assert shared_key_of(key) == "dom:sql/%2Froot/cat/ns/env/t.orders"
        assert shared_key_of("garbage") is None
        assert shared_key_of("dom:sql/a/b") is None

    def test_normalize_lookup_key_forms(self) -> None:
        shared = "dom:sql/%2Froot/cat/ns/env/t.orders"
        identity = "dom:sql/repo1/%2Froot/cat/ns/env/t.orders"
        canonical = f"{identity}/42"
        assert normalize_lookup_key(shared) == shared
        assert normalize_lookup_key(identity) == shared
        assert normalize_lookup_key(canonical) == shared
        with pytest.raises(InvalidKeyError):
            normalize_lookup_key("dom:sql/x")
