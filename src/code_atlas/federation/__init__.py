"""Federação multi-repo (lf-0118, Cycle 7 Phase 10).

Workspaces nomeados agrupam >=2 repositórios já indexados; cada membro
pode pinar um snapshot explícito (o default é o READY mais recente —
registrado na view, nunca presumido "mesmo tempo"). A leitura federada
junta entidades de domínio por identidade lógica compartilhada e emite
arestas ``shared_entity`` **computadas** — nada é persistido além das
tabelas v10, nenhuma relação é inventada e o isolamento por snapshot
entre repositórios é preservado por construção.
"""

from code_atlas.federation.federation import (
    Federation,
    normalize_lookup_key,
    shared_key_of,
)
from code_atlas.federation.model import (
    FEDERATION_RESOLVER_ID,
    MAX_GRAPH_DEPTH,
    MIN_FEDERATION_MEMBERS,
    VIEW_ID_FORMAT,
    EntityNode,
    FederatedEdge,
    FederatedGraph,
    FederatedView,
    FederationError,
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

__all__ = [
    "FEDERATION_RESOLVER_ID",
    "MAX_GRAPH_DEPTH",
    "MIN_FEDERATION_MEMBERS",
    "VIEW_ID_FORMAT",
    "EntityNode",
    "FederatedEdge",
    "FederatedGraph",
    "FederatedView",
    "Federation",
    "FederationError",
    "FederationMember",
    "FederationStore",
    "FederationWorkspace",
    "InvalidKeyError",
    "InvalidPinError",
    "MemberNotFoundError",
    "RepositoryNotFoundError",
    "SharedOccurrence",
    "SnapshotNotFoundError",
    "SnapshotNotReadyError",
    "ViewMember",
    "WorkspaceExistsError",
    "WorkspaceNotFoundError",
    "WorkspaceSizeError",
    "compute_view_id",
    "normalize_lookup_key",
    "shared_key_of",
]
