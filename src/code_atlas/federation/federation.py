"""Engine de federação multi-repo (lf-0118, Cycle 7 Phase 10).

Superfície de **leitura** sobre repositórios já indexados — nenhuma
escrita em ``domain_*``/``snapshots``/``relations``, nenhuma execução,
nenhuma descoberta de diretórios fora dos repos já registrados. As
únicas escritas do pacote são as tabelas v10 (workspaces/membros) e só
são alcançadas pela CLI — a API serve conexão ``read_only``.

Regras inegociáveis:

- **isolamento por snapshot**: cada membro resolve seu próprio
  ``snapshot_id``; todas as leituras de domínio são ancoradas por
  ``deo.snapshot_id = <snapshot do membro>`` — trocar o snapshot de um
  membro nunca toca os dados de outro;
- **identidade compartilhada honesta**: o join cross-repo é a
  ``identity_key`` lógica (``DomainIdentity.logical_key``) **menos** o
  segmento ``repository`` — ``shared_key``. Mesmo nome em catálogo/
  namespace/env/root distintos NÃO casa (é o motivo da identidade
  qualificada existir);
- **arestas federadas são computadas**: ``shared_entity`` liga
  entidades de repos distintos com a mesma ``shared_key`` — derivadas
  da view, nunca persistidas; ``computed=True`` + ``resolver=
  federation-view`` declaram a proveniência;
- **view honesta**: ``view_id`` é o hash dos pares
  ``(repo_id, snapshot_id|None)`` — pin, override ou READY novo mudam a
  identidade da view.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from pathlib import Path

from code_atlas.contracts.enums import SnapshotStatus
from code_atlas.discovery.identity import identity_key_for_root
from code_atlas.federation.model import (
    MAX_GRAPH_DEPTH,
    MIN_FEDERATION_MEMBERS,
    EntityNode,
    FederatedEdge,
    FederatedGraph,
    FederatedView,
    FederationMember,
    FederationWorkspace,
    InvalidKeyError,
    InvalidPinError,
    MemberNotFoundError,
    RepositoryNotFoundError,
    SharedOccurrence,
    SnapshotNotFoundError,
    SnapshotNotReadyError,
    ViewMember,
    WorkspaceExistsError,
    WorkspaceNotFoundError,
    WorkspaceSizeError,
    compute_view_id,
)
from code_atlas.federation.store import FederationStore
from code_atlas.storage.store import Store

FEDERATION_RESOLVER_ID = "federation-view"
MAX_WORKSPACE_NAME = 120

# Snapshots que uma view pode ler: READY (publicado) ou STALE (pin
# legítimo — congelar uma versão antiga é o propósito do pin). BUILDING
# é transiente; FAILED não tem dados confiáveis.
_READABLE_STATUSES = frozenset(
    {str(SnapshotStatus.READY), str(SnapshotStatus.STALE)}
)


def shared_key_of(identity_key: str) -> str | None:
    """``identity_key`` lógica sem o segmento ``repository``.

    ``dom:<domain>/<repo>/<root>/<catalog>/<ns>/<env>/<qname>[/<snap>]``
    → ``dom:<domain>/<root>/<catalog>/<ns>/<env>/<qname>``. O segmento
    repo é o índice 1 — separadores dentro de componentes são
    percent-encodados, logo split em ``/`` é seguro. ``None`` quando a
    chave não é ``dom:`` bem-formada (nunca fabrica identidade).
    """
    parts = identity_key.split("/")
    if len(parts) < 7 or not parts[0].startswith("dom:"):
        return None
    return "/".join([parts[0], *parts[2:7]])


def normalize_lookup_key(key: str) -> str:
    """Normaliza a chave pedida para a forma compartilhada (6 segmentos).

    Aceita a ``identity_key`` completa (7 segmentos — repo descartado),
    a ``canonical_key`` (8 — repo e snapshot descartados) ou a forma
    compartilhada já derivada (6). Qualquer outra forma é
    ``InvalidKeyError`` — nunca match por substring/nome nu.
    """
    parts = key.split("/")
    if not parts or not parts[0].startswith("dom:"):
        raise InvalidKeyError(f"not a domain identity key: {key[:80]!r}")
    if len(parts) == 6:
        return key
    if len(parts) in (7, 8):
        return "/".join([parts[0], *parts[2:7]])
    raise InvalidKeyError(
        f"domain identity key expects 6-8 segments, got {len(parts)}"
    )


class Federation:
    """Fachada de federação sobre uma conexão sqlite3.

    Métodos ``create_workspace``/``add_member``/``pin_member``/
    ``unpin_member`` escrevem — cada um na própria transação curta
    (``with conn:``). Todos os demais são somente leitura.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._store = Store(conn)
        self._fstore = FederationStore(conn)

    # --- workspaces (CRUD — superfície CLI) ---------------------------

    def create_workspace(self, name: str) -> FederationWorkspace:
        """Cria workspace nomeado; nome duplicado → ``WorkspaceExistsError``."""
        clean = name.strip()
        if not clean or len(clean) > MAX_WORKSPACE_NAME:
            raise InvalidPinError(
                f"workspace name must be 1..{MAX_WORKSPACE_NAME} chars"
            )
        with self._conn:
            try:
                ws_id = self._fstore.create_workspace(clean)
            except sqlite3.IntegrityError as exc:
                raise WorkspaceExistsError(
                    f"federation workspace already exists: {clean!r}"
                ) from exc
        return FederationWorkspace(id=ws_id, name=clean, created_at="")

    def get_workspace(self, ref: str | int) -> FederationWorkspace:
        """Workspace por id (int/str dígito) ou nome — 404 tipado."""
        ws: FederationWorkspace | None = None
        if isinstance(ref, int) or str(ref).isdigit():
            ws = self._fstore.workspace_by_id(int(ref))
        if ws is None and not (isinstance(ref, int) or str(ref).isdigit()):
            ws = self._fstore.workspace_by_name(str(ref))
        if ws is None:
            raise WorkspaceNotFoundError(f"federation workspace: {ref!r}")
        return ws

    def list_workspaces(self) -> list[FederationWorkspace]:
        return self._fstore.list_workspaces()

    def members_of(self, workspace_id: int) -> list[FederationMember]:
        return self._fstore.list_members(workspace_id)

    # --- members ------------------------------------------------------

    def add_member(
        self, ws: FederationWorkspace, repository_id: int
    ) -> FederationMember:
        """Registra repo no workspace — idempotente (UNIQUE + OR IGNORE).

        Rejeita repo inexistente: federação só agrupa repositórios **já
        indexados** — nenhuma descoberta de diretório, nenhum registro
        implícito.
        """
        if self._store.repository_by_id(repository_id) is None:
            raise RepositoryNotFoundError(
                f"repository not indexed: {repository_id}"
            )
        with self._conn:
            self._fstore.add_member(ws.id, repository_id)
        member = self._fstore.member_for(ws.id, repository_id)
        assert member is not None
        return member

    def remove_member(
        self, ws: FederationWorkspace, repository_id: int
    ) -> None:
        with self._conn:
            if not self._fstore.remove_member(ws.id, repository_id):
                raise MemberNotFoundError(
                    f"repository {repository_id} is not a member of "
                    f"{ws.name!r}"
                )

    def pin_member(
        self,
        ws: FederationWorkspace,
        repository_id: int,
        snapshot_id: int,
    ) -> FederationMember:
        """Pina o membro num snapshot legível **daquele repo**.

        READY e STALE são aceitos (pin = congelar versão); BUILDING/
        FAILED são rejeitados — pin nunca aponta para dado incompleto.
        """
        if self._fstore.member_for(ws.id, repository_id) is None:
            raise MemberNotFoundError(
                f"repository {repository_id} is not a member of {ws.name!r}"
            )
        snap = self._store.snapshot_by_id(snapshot_id)
        if snap is None or snap.repository_id != repository_id:
            raise SnapshotNotFoundError(
                f"snapshot {snapshot_id} not found for repository "
                f"{repository_id}"
            )
        if str(snap.status) not in _READABLE_STATUSES:
            raise SnapshotNotReadyError(
                f"snapshot {snapshot_id} is {snap.status}, "
                "cannot pin an unreadable snapshot"
            )
        with self._conn:
            self._fstore.set_pinned_snapshot(ws.id, repository_id, snapshot_id)
        member = self._fstore.member_for(ws.id, repository_id)
        assert member is not None
        return member

    def unpin_member(
        self, ws: FederationWorkspace, repository_id: int
    ) -> FederationMember:
        """Limpa o pin — membro volta a seguir o READY mais recente."""
        if self._fstore.member_for(ws.id, repository_id) is None:
            raise MemberNotFoundError(
                f"repository {repository_id} is not a member of {ws.name!r}"
            )
        with self._conn:
            self._fstore.set_pinned_snapshot(ws.id, repository_id, None)
        member = self._fstore.member_for(ws.id, repository_id)
        assert member is not None
        return member

    def resolve_repository(self, ref: str) -> int:
        """``<repo-path-or-id>`` → repository_id já indexado.

        Ordem: id numérico → ``identity_key``/``root_handle`` literal →
        path on-disk (probe git de ``identity_key_for_root`` + fallback
        ``root_handle`` resolvido). Nada é criado: referência que não
        resolve para repo indexado é ``RepositoryNotFoundError``.
        """
        if ref.isdigit():
            rid = int(ref)
            if self._store.repository_by_id(rid) is not None:
                return rid
            raise RepositoryNotFoundError(f"repository not indexed: {rid}")
        resolved = self._store.repository_id_for(ref)
        if resolved is not None:
            return resolved
        candidate = Path(ref).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        candidate = candidate.resolve()
        resolved = self._store.repository_id_for(
            identity_key_for_root(candidate))
        if resolved is not None:
            return resolved
        resolved = self._store.repository_id_for(str(candidate))
        if resolved is not None:
            return resolved
        raise RepositoryNotFoundError(
            f"no indexed repository for {ref!r} — index it first"
        )

    # --- view -----------------------------------------------------------

    def resolve_view(
        self,
        ws: FederationWorkspace,
        pins: Mapping[int, int] | None = None,
    ) -> FederatedView:
        """Materializa ``{repo_id: snapshot_id}`` do workspace.

        Precedência por membro: ``pins`` (override do request) → pin
        persistido → READY mais recente. Pins de override exigem repo
        membro + snapshot legível do mesmo repo; um pin persistido é
        honrado mesmo que o snapshot tenha virado STALE (congelar é o
        propósito). Membros sem snapshot legível entram na view como
        ``snapshot_id=None`` — ausência declarada, nunca inventada.
        """
        members = self._fstore.list_members(ws.id)
        if len(members) < MIN_FEDERATION_MEMBERS:
            raise WorkspaceSizeError(
                f"federation {ws.name!r} has {len(members)} member(s) — "
                f"requires >= {MIN_FEDERATION_MEMBERS} indexed repositories"
            )
        overrides = dict(pins or {})
        member_repos = {m.repository_id for m in members}
        extra = sorted(set(overrides) - member_repos)
        if extra:
            raise InvalidPinError(
                f"pin override for non-member repositories: {extra}"
            )
        view_members: list[ViewMember] = []
        resolved: dict[int, int | None] = {}
        for m in members:
            vm = self._resolve_member(m, overrides.get(m.repository_id))
            view_members.append(vm)
            resolved[m.repository_id] = vm.snapshot_id
        return FederatedView(
            workspace_id=ws.id,
            workspace_name=ws.name,
            view_id=compute_view_id(resolved),
            members=tuple(view_members),
        )

    def _resolve_member(
        self, member: FederationMember, override: int | None
    ) -> ViewMember:
        if override is not None:
            snap = self._store.snapshot_by_id(override)
            if snap is None or snap.repository_id != member.repository_id:
                raise SnapshotNotFoundError(
                    f"snapshot {override} not found for repository "
                    f"{member.repository_id}"
                )
            if str(snap.status) not in _READABLE_STATUSES:
                raise SnapshotNotReadyError(
                    f"snapshot {override} is {snap.status}, "
                    "not readable"
                )
            return ViewMember(
                repository_id=member.repository_id,
                snapshot_id=snap.id,
                pinned=True,
                resolution="pin-override",
                snapshot_status=str(snap.status),
            )
        if member.pinned_snapshot_id is not None:
            snap = self._store.snapshot_by_id(member.pinned_snapshot_id)
            if snap is not None and snap.repository_id == member.repository_id:
                return ViewMember(
                    repository_id=member.repository_id,
                    snapshot_id=snap.id,
                    pinned=True,
                    resolution="pinned",
                    snapshot_status=str(snap.status),
                )
            # pin apontando para snapshot inexistente/alheio só é
            # possível via adulteração manual — reporta, não adivinha.
            return ViewMember(
                repository_id=member.repository_id,
                snapshot_id=None,
                pinned=True,
                resolution="pin-invalid",
                snapshot_status="",
            )
        snap = self._store.latest_ready_snapshot(member.repository_id)
        if snap is None:
            return ViewMember(
                repository_id=member.repository_id,
                snapshot_id=None,
                pinned=False,
                resolution="no-ready-snapshot",
                snapshot_status="",
            )
        return ViewMember(
            repository_id=member.repository_id,
            snapshot_id=snap.id,
            pinned=False,
            resolution="latest-ready",
            snapshot_status=str(snap.status),
        )

    # --- leitura federada -------------------------------------------------

    def _member_occurrences(
        self, view: FederatedView
    ) -> dict[int, list[SharedOccurrence]]:
        """Ocorrências de domínio por membro — ancoradas no snapshot da view."""
        out: dict[int, list[SharedOccurrence]] = {}
        for repo_id, snap_id in view.snapshot_map.items():
            occs: list[SharedOccurrence] = []
            for row in self._fstore.snapshot_entity_occurrences(snap_id):
                skey = shared_key_of(str(row["identity_key"]))
                if skey is None:
                    continue  # chave corrompida — nunca fabrica identidade
                occs.append(
                    SharedOccurrence(
                        repository_id=repo_id,
                        snapshot_id=snap_id,
                        entity_id=int(row["entity_id"]),
                        occurrence_id=int(row["occ_id"]),
                        domain=str(row["domain"]),
                        kind=str(row["kind"]),
                        identity_key=str(row["identity_key"]),
                        shared_key=skey,
                        qualified_name=str(row["qualified_name"]),
                        name=str(row["name"]),
                        catalog=str(row["catalog"]),
                        namespace=str(row["namespace"]),
                        environment=str(row["environment"]),
                        path=str(row["source_path"] or ""),
                        line=(
                            None if row["line"] is None else int(row["line"])
                        ),
                        col=(None if row["col"] is None else int(row["col"])),
                        extraction_status=str(row["extraction_status"]),
                    )
                )
            out[repo_id] = occs
        return out

    def entity_occurrences(
        self,
        ws: FederationWorkspace,
        key: str,
        pins: Mapping[int, int] | None = None,
    ) -> dict[str, object]:
        """Quais repos contêm a identidade pedida — ocorrências por repo."""
        skey = normalize_lookup_key(key)
        view = self.resolve_view(ws, pins)
        items: list[SharedOccurrence] = []
        for occs in self._member_occurrences(view).values():
            items.extend(o for o in occs if o.shared_key == skey)
        items.sort(key=lambda o: (o.repository_id, o.entity_id, o.occurrence_id))
        repos = sorted({o.repository_id for o in items})
        payload = view.to_dict()
        payload.update(
            {
                "key": key,
                "shared_key": skey,
                "items": [o.to_dict() for o in items],
                "repositories_with_entity": repos,
                "total": len(items),
            }
        )
        return payload

    def graph(
        self,
        ws: FederationWorkspace,
        depth: int = 1,
        limit: int = 500,
        pins: Mapping[int, int] | None = None,
    ) -> FederatedGraph:
        """Grafo federado: sementes compartilhadas + BFS limitado.

        Sementes = entidades cuja ``shared_key`` aparece em >=2 repos
        membros (o único vínculo cross-repo honesto desta fase).
        ``depth`` = hops de BFS sobre arestas intra-snapshot
        (``domain_relations`` entidade↔entidade, ambas as direções) **e**
        links ``shared_entity``; ``limit`` encima nós+arestas —
        ``truncated`` declara o corte.
        """
        depth = max(0, min(int(depth), MAX_GRAPH_DEPTH))
        view = self.resolve_view(ws, pins)
        per_repo = self._member_occurrences(view)
        entities, entity_repo, entity_paths, shared_repos, occ_to_entity = (
            _collect_entities(per_repo))

        # arestas intra-snapshot (entidade↔entidade apenas — a camada de
        # domínio desta fase não expande endpoints de símbolo)
        adjacency: dict[int, set[int]] = {}
        intra = self._intra_edges(view, occ_to_entity, adjacency)
        shared_edges, seeds = _shared_edges(
            per_repo, shared_repos, adjacency)

        # BFS a partir das sementes — hops contam intra e shared.
        visited, truncated = _bfs_expand(seeds, adjacency, depth, limit)
        node_ids = sorted(visited)[:limit]
        node_set = set(node_ids)
        edges = [
            e for e in intra + shared_edges
            if e.from_entity_id in node_set and e.to_entity_id in node_set
        ]
        if len(edges) > limit:
            edges = edges[:limit]
            truncated = True
        nodes = tuple(
            EntityNode(
                entity_id=eid,
                repository_id=entity_repo[eid],
                snapshot_id=view.snapshot_map[entity_repo[eid]],
                domain=entities[eid].domain,
                kind=entities[eid].kind,
                identity_key=entities[eid].identity_key,
                shared_key=entities[eid].shared_key,
                qualified_name=entities[eid].qualified_name,
                name=entities[eid].name,
                paths=tuple(sorted(entity_paths.get(eid, ()))),
            )
            for eid in node_ids
        )
        stats = {
            "member_repositories": len(view.snapshot_map),
            "intra_edges": len(intra),
            "shared_edges": len(shared_edges),
            "candidate_entities": len(entities),
        }
        return FederatedGraph(
            view=view,
            nodes=nodes,
            edges=tuple(edges),
            depth=depth,
            seeds=len(seeds),
            truncated=truncated,
            stats=stats,
        )

    def _intra_edges(
        self,
        view: FederatedView,
        occ_to_entity: Mapping[int, tuple[int, int]],
        adjacency: dict[int, set[int]],
    ) -> list[FederatedEdge]:
        """Arestas reais de ``domain_relations`` dentro de cada snapshot."""
        intra: list[FederatedEdge] = []
        seen: set[tuple[int, int, str]] = set()
        for snap_id in view.snapshot_map.values():
            for r in self._store.domain_relations_snapshot(snap_id):
                fe, te = r["from_entity_occ_id"], r["to_entity_occ_id"]
                if fe is None or te is None:
                    continue
                src = occ_to_entity.get(int(fe))
                dst = occ_to_entity.get(int(te))
                if src is None or dst is None:
                    continue
                edge_key = (src[1], dst[1], str(r["type"]))
                if edge_key in seen:
                    continue
                seen.add(edge_key)
                intra.append(
                    FederatedEdge(
                        kind="intra",
                        relation_id=int(r["id"]),
                        from_entity_id=src[1],
                        to_entity_id=dst[1],
                        from_repo=src[0],
                        to_repo=dst[0],
                        relation_type=str(r["type"]),
                        resolution_status=str(r["resolution_status"]),
                        confidence_class=str(r["confidence_class"]),
                        resolver=str(r["resolver"]),
                    )
                )
                adjacency.setdefault(src[1], set()).add(dst[1])
                adjacency.setdefault(dst[1], set()).add(src[1])
        return intra


def _collect_entities(
    per_repo: Mapping[int, list[SharedOccurrence]],
) -> tuple[
    dict[int, SharedOccurrence],
    dict[int, int],
    dict[int, set[str]],
    dict[str, set[int]],
    dict[int, tuple[int, int]],
]:
    """Índices por entidade a partir das ocorrências por membro."""
    occ_to_entity: dict[int, tuple[int, int]] = {}  # occ → (repo, ent)
    entities: dict[int, SharedOccurrence] = {}
    entity_paths: dict[int, set[str]] = {}
    shared_repos: dict[str, set[int]] = {}
    entity_repo: dict[int, int] = {}
    for repo_id, occs in per_repo.items():
        for o in occs:
            occ_to_entity[o.occurrence_id] = (repo_id, o.entity_id)
            entities[o.entity_id] = o
            entity_repo[o.entity_id] = repo_id
            if o.path:
                entity_paths.setdefault(o.entity_id, set()).add(o.path)
            shared_repos.setdefault(o.shared_key, set()).add(repo_id)
    return entities, entity_repo, entity_paths, shared_repos, occ_to_entity


def _shared_edges(
    per_repo: Mapping[int, list[SharedOccurrence]],
    shared_repos: Mapping[str, set[int]],
    adjacency: dict[int, set[int]],
) -> tuple[list[FederatedEdge], set[int]]:
    """``shared_entity`` computadas: mesma shared_key em repos distintos."""
    edges: list[FederatedEdge] = []
    seeds: set[int] = set()
    seen: set[frozenset[int]] = set()
    shared_entities = {
        skey for skey, repos in shared_repos.items() if len(repos) > 1
    }
    for skey in sorted(shared_entities):
        members = [
            o for occs in per_repo.values()
            for o in occs if o.shared_key == skey
        ]
        for i, a in enumerate(members):
            seeds.add(a.entity_id)
            for b in members[i + 1:]:
                if a.repository_id == b.repository_id:
                    continue
                pair = frozenset((a.entity_id, b.entity_id))
                if pair in seen:
                    continue
                seen.add(pair)
                edges.append(
                    FederatedEdge(
                        kind="shared_entity",
                        from_entity_id=a.entity_id,
                        to_entity_id=b.entity_id,
                        from_repo=a.repository_id,
                        to_repo=b.repository_id,
                        relation_type="shared_entity",
                        resolution_status="resolved",
                        confidence_class="resolved",
                        resolver=FEDERATION_RESOLVER_ID,
                        computed=True,
                    )
                )
                adjacency.setdefault(a.entity_id, set()).add(b.entity_id)
                adjacency.setdefault(b.entity_id, set()).add(a.entity_id)
    return edges, seeds


def _bfs_expand(
    seeds: set[int],
    adjacency: Mapping[int, set[int]],
    depth: int,
    limit: int,
) -> tuple[set[int], bool]:
    """BFS limitado: ``depth`` hops, ``limit`` nós — retorna visitados."""
    visited: set[int] = set(seeds)
    frontier = set(seeds)
    hops = 0
    while frontier and hops < depth:
        hops += 1
        nxt: set[int] = set()
        for ent in sorted(frontier):
            for nb in adjacency.get(ent, ()):
                if nb not in visited:
                    if len(visited) >= limit:
                        return visited, True
                    visited.add(nb)
                    nxt.add(nb)
        frontier = nxt
    return visited, False

