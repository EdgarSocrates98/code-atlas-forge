"""Modelo do workspace federado (lf-0118, Cycle 7 Phase 10).

Um ``FederationWorkspace`` agrupa >=2 ``repository_id`` já indexados.
Cada ``FederationMember`` pode carregar um ``pinned_snapshot_id``
explícito; quando o pin está vazio a view resolve o READY mais recente
**naquele momento** — registrado na view, nunca presumido "mesmo tempo"
entre repositórios.

``FederatedView`` é a materialização ``{repo_id: snapshot_id}``:
``view_id`` é a **chave de cache** da view (lf-0134) — hash sha256 do
contrato completo de identidade: membership (``repo_id`` +
``identity_key``), o snapshot resolvido de cada membro (pin/None), o
digest das ``analyzer_versions`` que produziram os dados daquele
snapshot (adapters/resolvers de índice), a versão do resolver
federado e o ``schema_version`` do storage. Qualquer componente que
mude invalida a view — caches chaveados em ``view_id`` nunca servem
rows de uma identidade diferente.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field


class FederationError(Exception):
    """Erro tipado da camada de federação."""


class WorkspaceNotFoundError(FederationError):
    """Workspace inexistente (por id ou nome)."""


class WorkspaceExistsError(FederationError):
    """Nome de workspace já registrado (UNIQUE)."""


class WorkspaceSizeError(FederationError):
    """Federação exige >=2 membros — views não existem abaixo disso."""


class MemberNotFoundError(FederationError):
    """Repositório não é membro do workspace."""


class RepositoryNotFoundError(FederationError):
    """Repo id/path não corresponde a repositório indexado."""


class SnapshotNotFoundError(FederationError):
    """Snapshot inexistente (ou fora do repositório do membro)."""


class SnapshotNotReadyError(FederationError):
    """Snapshot existe mas não é legível (BUILDING/FAILED)."""


class InvalidPinError(FederationError):
    """Pin malformado ou fora do escopo do workspace."""


class InvalidKeyError(FederationError):
    """Chave de identidade federada malformada."""


MIN_FEDERATION_MEMBERS = 2
MAX_GRAPH_DEPTH = 8
DEFAULT_GRAPH_LIMIT = 500
MAX_GRAPH_LIMIT = 5000

FEDERATION_RESOLVER_ID = "federation-view"

# Versão do contrato hasheado em ``view_id``. Bump quando o payload da
# identidade muda — views computadas por outro formato nunca colidem,
# o que torna a invalidação de caches externos chaveados em ``view_id``
# honesta por construção (lf-0134: formato 1 era só os pares
# ``(repo_id, snapshot_id)`` do Cycle 7 — sem identidade do repo,
# versões de adapter/resolver nem schema).
VIEW_ID_FORMAT = 2


@dataclass(frozen=True)
class FederationWorkspace:
    """Agrupamento nomeado de repositórios (row de ``federation_workspaces``)."""

    id: int
    name: str
    created_at: str


@dataclass(frozen=True)
class FederationMember:
    """Membro de um workspace (row de ``federation_members``).

    ``pinned_snapshot_id=None`` = membro segue o READY mais recente —
    registrado na view a cada resolução.
    """

    id: int
    workspace_id: int
    repository_id: int
    pinned_snapshot_id: int | None
    added_at: str


@dataclass(frozen=True)
class ViewMember:
    """Resolução de um membro dentro de uma ``FederatedView``.

    ``resolution`` declara a origem do snapshot escolhido — honesta por
    construção: ``pin-override`` (request), ``pinned`` (persistido),
    ``latest-ready`` (default) ou ``no-ready-snapshot`` (ausente).
    """

    repository_id: int
    snapshot_id: int | None
    pinned: bool
    resolution: str  # pin-override | pinned | latest-ready | no-ready-snapshot
    snapshot_status: str = ""  # status atual do snapshot escolhido


@dataclass(frozen=True)
class FederatedView:
    """Mapa ``{repository_id: snapshot_id}`` materializado — read-only.

    ``view_id`` é ``fedview:<sha256>`` do payload de identidade
    completo (ver ``compute_view_id``) cobrindo TODOS os membros —
    membros sem snapshot resolvido entram como ``null`` na posição de
    snapshot: a ausência também faz parte da identidade da view.
    """

    workspace_id: int
    workspace_name: str
    view_id: str
    members: tuple[ViewMember, ...]

    @property
    def snapshot_map(self) -> dict[int, int]:
        """``{repository_id: snapshot_id}`` dos membros resolvidos."""
        return {
            m.repository_id: m.snapshot_id
            for m in self.members
            if m.snapshot_id is not None
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "workspace": self.workspace_name,
            "view_id": self.view_id,
            "members": [
                {
                    "repository_id": m.repository_id,
                    "snapshot_id": m.snapshot_id,
                    "pinned": m.pinned,
                    "resolution": m.resolution,
                    "snapshot_status": m.snapshot_status,
                }
                for m in self.members
            ],
        }


def compute_view_id(
    pairs: Mapping[int, int | None],
    *,
    identities: Mapping[int, str] | None = None,
    member_versions: Mapping[int, str] | None = None,
    schema: int = 0,
) -> str:
    """Hash determinístico da identidade completa da view — a cache key.

    Componentes (lf-0134, audit de cache):

    - **membership**: ``repo_id`` de cada membro (chaves de ``pairs``)
      — add/remove de membro muda a lista;
    - **identidade do repo**: ``identities[repo_id]`` = ``identity_key``
      lógica — um re-bind de identidade sob o mesmo ``repo_id`` invalida;
    - **snapshot pins**: ``pairs[repo_id]`` (``None`` declarado) —
      pin/override/READY novo muda a posição resolvida;
    - **versões de resolver/adapter**: ``member_versions[repo_id]`` =
      digest das ``analyzer_versions`` do snapshot resolvido (a versão
      dos adapters/resolvers que produziram os dados) + o próprio
      ``FEDERATION_RESOLVER_ID``/``VIEW_ID_FORMAT`` do caminho de
      leitura federado;
    - **schema**: ``schema`` = ``schema_version`` do storage.

    Ordenação por ``repo_id`` — mesma resolução, mesmo ``view_id``,
    qualquer ordem de inserção. Componentes não fornecidos entram como
    vazio/0 — degradação declarada, nunca fabricada.
    """
    ids = dict(identities or {})
    vers = dict(member_versions or {})
    ordered = sorted(pairs.items(), key=lambda kv: int(kv[0]))
    members = [
        [
            int(r),
            str(ids.get(int(r), "")),
            (None if s is None else int(s)),
            str(vers.get(int(r), "")),
        ]
        for r, s in ordered
    ]
    payload = {
        "format": VIEW_ID_FORMAT,
        "resolver": FEDERATION_RESOLVER_ID,
        "schema": int(schema),
        "members": members,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"fedview:{digest}"


@dataclass(frozen=True)
class SharedOccurrence:
    """Ocorrência de entidade de domínio vista pela federação."""

    repository_id: int
    snapshot_id: int
    entity_id: int
    occurrence_id: int
    domain: str
    kind: str
    identity_key: str      # chave lógica completa (repo-scoped)
    shared_key: str        # chave sem o segmento repository (join federado)
    qualified_name: str
    name: str
    catalog: str
    namespace: str
    environment: str
    path: str
    line: int | None
    col: int | None
    extraction_status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "repository_id": self.repository_id,
            "snapshot_id": self.snapshot_id,
            "entity_id": self.entity_id,
            "occurrence_id": self.occurrence_id,
            "domain": self.domain,
            "kind": self.kind,
            "identity_key": self.identity_key,
            "shared_key": self.shared_key,
            "qualified_name": self.qualified_name,
            "name": self.name,
            "catalog": self.catalog,
            "namespace": self.namespace,
            "environment": self.environment,
            "path": self.path,
            "line": self.line,
            "col": self.col,
            "extraction_status": self.extraction_status,
        }


@dataclass(frozen=True)
class FederatedEdge:
    """Aresta do grafo federado.

    ``kind`` = ``intra`` (row real de ``domain_relations`` dentro de um
    snapshot-membro) ou ``shared_entity`` (aresta computada: mesma
    identidade compartilhada observada em repositórios distintos).
    Arestas ``shared_entity`` são derivadas da view — nunca persistidas:
    ``computed=True`` declara a proveniência honesta.
    """

    kind: str               # intra | shared_entity
    from_entity_id: int
    to_entity_id: int
    from_repo: int
    to_repo: int
    relation_type: str
    resolution_status: str
    confidence_class: str
    resolver: str
    relation_id: int | None = None
    computed: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "relation_id": self.relation_id,
            "from_entity_id": self.from_entity_id,
            "to_entity_id": self.to_entity_id,
            "from_repository_id": self.from_repo,
            "to_repository_id": self.to_repo,
            "type": self.relation_type,
            "resolution_status": self.resolution_status,
            "confidence_class": self.confidence_class,
            "resolver": self.resolver,
            "computed": self.computed,
        }


@dataclass(frozen=True)
class EntityNode:
    """Nó do grafo federado — ``(repository_id, entity)`` observado."""

    entity_id: int
    repository_id: int
    snapshot_id: int
    domain: str
    kind: str
    identity_key: str
    shared_key: str
    qualified_name: str
    name: str
    paths: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "entity_id": self.entity_id,
            "repository_id": self.repository_id,
            "snapshot_id": self.snapshot_id,
            "domain": self.domain,
            "kind": self.kind,
            "identity_key": self.identity_key,
            "shared_key": self.shared_key,
            "qualified_name": self.qualified_name,
            "name": self.name,
            "paths": list(self.paths),
        }


@dataclass(frozen=True)
class FederatedGraph:
    """Resultado do traversal federado — nós/arestas + flags honestas."""

    view: FederatedView
    nodes: tuple[EntityNode, ...]
    edges: tuple[FederatedEdge, ...]
    depth: int
    seeds: int
    truncated: bool
    stats: Mapping[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        out = self.view.to_dict()
        out.update(
            {
                "nodes": [n.to_dict() for n in self.nodes],
                "edges": [e.to_dict() for e in self.edges],
                "depth": self.depth,
                "seed_entities": self.seeds,
                "truncated": self.truncated,
                "stats": dict(self.stats),
            }
        )
        return out
