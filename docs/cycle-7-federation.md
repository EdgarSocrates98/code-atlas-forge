# Cycle 7 — Multi-repo federation (lf-0118, Phase 10)

Status: implemented on `cycle-7-domain-intelligence-federation`. The
federation layer is **read-oriented over already-indexed repositories
with per-member snapshot pinning** — it never indexes, never writes
outside its two tables, never auto-discovers directories, and never
executes code/jobs/Terraform/pipelines or contacts external systems.

A `FederationWorkspace` groups ≥2 `repository_id`s already present in
`repositories`. A `FederatedView` materializes `{repo_id: snapshot_id}`
— one explicit READY-or-STALE snapshot per member — and every domain
read is anchored to that member's resolved snapshot. Isolation between
repositories is preserved by construction: changing one member's pin
cannot touch another member's selected snapshot or data.

## Layout

| File | Role |
|---|---|
| `src/code_atlas/storage/migrations.py` | Additive **schema v10**: `federation_workspaces(name UNIQUE, created_at)` + `federation_members(workspace_id, repository_id, pinned_snapshot_id NULLABLE, added_at)` with FKs, `UNIQUE(workspace_id, repository_id)`, membership indexes. No earlier migration touched |
| `src/code_atlas/federation/model.py` | Dataclasses + typed errors; `compute_view_id` = `fedview:<sha256>` of sorted `(repo_id, snapshot_id|None)` pairs |
| `src/code_atlas/federation/store.py` | `FederationStore` — parameterized CRUD on the two v10 tables + `snapshot_entity_occurrences(snapshot_id)` (occurrence+entity+path join, always snapshot-scoped) |
| `src/code_atlas/federation/federation.py` | `Federation` engine — workspace CRUD, member/pin validation, view resolution, `entity_occurrences`, bounded `graph`; `shared_key_of`/`normalize_lookup_key` |
| `src/code_atlas/api/handlers.py` | GET-only routes under `/federation/workspaces*`; typed `ApiError` mapping; `_route_federation` dispatcher; `_parse_pins` bounded parser |
| `src/code_atlas/cli.py` | `code-atlas federation create|add|pin|view` — the **only** mutation surface |
| `tests/federation/` | Fixture with two real indexed repos (shared OpenAPI `GET /v1/orders`, shared `mart.orders`, divergent `other.orders`, intra-only `mart.v_orders`) + engine/CLI suites |
| `tests/api/test_federation_api.py` | Route contract incl. pin overrides, 400/404 typing, mutation routes absent |

## The rules that matter

**Never match on bare name — across repos it is `shared_key`.** The
cross-repository join key is `identity_key` minus its `repository`
segment: `dom:<domain>/<root>/<catalog>/<ns>/<env>/<qname>`. Same name
with different catalog/namespace/environment/root does not collide —
`other.orders` never links to `mart.orders`. Lookups accept the shared
key, full `identity_key`, or `canonical_key`; anything else is
`InvalidKeyError` — never a substring/name match.

**Snapshot isolation is per member.** Every occurrence query carries
`deo.snapshot_id = <that member's resolved snapshot>`. Pinning member A
to an older snapshot hides A's newer entities while member B keeps
serving its own latest READY — tested end to end.

**Honest view identity.** `view_id` hashes all resolved pairs including
`None` (no readable snapshot is declared, not presumed). Resolution
precedence per member: request pin-override → persisted pin → latest
READY; `resolution` records which source produced the snapshot, and
members without a readable snapshot surface as `snapshot_id=null`
(`no-ready-snapshot` / `pin-invalid`).

**Federated edges are computed, never persisted.** `shared_entity`
edges join entities sharing a `shared_key` across *different* member
repos (`computed=true`, `resolver=federation-view`, `relation_id=null`).
Nothing is inserted into `domain_relations`, and no cross-repo relation
of any other kind is fabricated — `intra` edges are exactly the
persisted entity↔entity rows of each member's own snapshot.

**Bounded traversal.** `graph` seeds = entities whose `shared_key` is
observed in ≥2 member repos; BFS then expands over `intra` + `shared`
adjacency (bidirectional) capped at `MAX_GRAPH_DEPTH=8` and
`limit ≤ 5000`; `truncated` declares the cut.

**HTTP is read-only by design.** Workspace create/add/pin exist only on
the CLI; POST/PUT/DELETE under `/federation/*` have no route (404/405/
501). Handlers run on the server's `query_only` read connection —
workspace mutations would fail loudly there anyway.

**Members are indexed repositories only.** `add`/`pin` accept repo id,
`identity_key`, `root_handle`, or an on-disk path — the path is resolved
to an existing `repositories` row via `identity_key_for_root`; nothing
is scanned or registered.

## Pins

`pinned_snapshot_id` may reference a READY **or STALE** snapshot of the
*same* repository (freezing an old version is the point of a pin).
BUILDING/FAILED are rejected (`SnapshotNotReadyError`); foreign-repo or
nonexistent snapshots are rejected (`SnapshotNotFoundError`). `unpin`
(`pin … latest` on the CLI) restores latest-READY tracking.

## API surface

```
GET /api/v1/federation/workspaces
GET /api/v1/federation/workspaces/{id}
GET /api/v1/federation/workspaces/{id}/view?pin=<repo>:<snap>[,…]
GET /api/v1/federation/workspaces/{id}/entities/{logical_key}/occurrences
GET /api/v1/federation/workspaces/{id}/graph?depth=0..8&limit<=5000
```

## CLI surface

```
code-atlas federation create <name>
code-atlas federation add <ws> <repo-path-or-id>
code-atlas federation pin <ws> <repo> <snapshot-id|latest>
code-atlas federation view <ws> [--pin <repo>:<snap>[,…]] [--json]
```

## Verification

- `pytest tests/ -q` — 924 passed, 1 skipped. The run must exclude the
  two **pre-existing untracked WIP dirs** `tests/domains/{aws,spark}`
  (out of scope): they break collection with a duplicate module name
  (`test_adapter_contract`) — untouched per scope.
- `ruff check src tests` — clean.
- `mypy src` — clean.
- Schema assertions updated 9→10 in `test_domain_storage.py` and
  `test_migration_tx.py` (incl. migration-name set).
