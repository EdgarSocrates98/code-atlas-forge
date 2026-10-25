# Cycle 8 — Federation scale + view cache correctness (lf-0134, Phase 8)

Status: implemented on `cycle-8-production-quality`. Scope: audit and fix
the federation **view cache key**, multi-repo (5-member) stress with
per-snapshot isolation, and point-in-time immutability of pinned views.
Numbers below are **measured** by `benchmarks/bench_federation_scale.py`
(full payload in `benchmarks/results/federation-scale-latest.json`) —
never targets. Fixture repos are intentionally small; the measured regime
is the N-way federation fan-out, not index size.

## Cache-key audit → fix

The federation view cache identity is `view_id` (`fedview:<sha256>`),
computed by `compute_view_id`. Any consumer keying on it — the search
cursor fingerprint (`query_fingerprint`, lf-0058/lf-0119), the API
payload, external clients — inherits its coverage.

**Audit of the Cycle-7 key** (`fedview:` over sorted
`(repo_id, snapshot_id|None)` pairs):

| required component | covered before? |
|---|---|
| membership | **yes** — member `repo_id`s are pair keys; add/remove changes the hash |
| repo identity | **partial** — only the surrogate `repo_id`; a re-bound `identity_key` under the same id was invisible to the key |
| snapshot pins | **yes** — resolved `snapshot_id` per member, `None` declared |
| resolver/adapter versions | **no** |
| schema version | **no** |

**Fix (format 2, `VIEW_ID_FORMAT = 2`).** `compute_view_id` now hashes a
structured payload:

```json
{"format": 2,
 "resolver": "federation-view",
 "schema": <schema_version(conn)>,
 "members": [[repo_id, identity_key, snapshot_id|null,
              sha256(snapshot.analyzer_versions)], …]}
```

- `membership` — the member set (keys of `pairs`);
- `repo identity` — `repositories.identity_key` per member, read in one
  batch query (`FederationStore.repo_identities`);
- `snapshot pins` — the resolved `snapshot_id`, `null` still declared;
- `resolver/adapter versions` — `sha256` of each resolved snapshot's
  `analyzer_versions` (parser/extractor/resolver/`domain:*` adapter
  versions that produced the rows) threaded through `_resolve_member`
  **without extra queries**, plus `FEDERATION_RESOLVER_ID` +
  `VIEW_ID_FORMAT` for the federation read path itself;
- `schema` — `schema_version(conn)`, one `MAX(version)` probe.

Components not supplied by a caller degrade to `""`/`0` — declared, never
fabricated. `ViewMember`/`FederatedView.to_dict()` payloads are unchanged;
only the `view_id` value carries the wider key. Same resolution ⇒ same
`view_id`, regardless of insertion order (sorted by `repo_id`).

**Regression coverage** (`tests/federation/test_federation_scale.py`,
`TestViewCacheKey`): tampering any single component — re-pin, member
add/remove, `identity_key` re-bind, `analyzer_versions` bump on the same
snapshot row, `schema_migrations` insert — produces a different `view_id`
while `snapshot_map` stays identical, and restores the original id when
reverted. A repinned view never carries rows of the previous snapshot:
`entity_occurrences` items are asserted `snapshot_id ==
view.snapshot_map[repo]` on both sides of a pin flip.

## Multi-repo scale (measured)

Five small deterministic repos (seed 20261025) indexed via the real CLI
(`code-atlas index --domains sql,api`) + `federation create/add`; shared
`mart.orders` + identical OpenAPI contract across all five, unique
`mart.t{i}`/`mart.v{i}` per repo. Setup (5 indexes + 4 workspaces):
~0.59 s. Median ms per call, 9 repeats:

| members | resolve_view | entity lookup | graph depth=1 | search (all members) |
|---|---|---|---|---|
| 2 | 0.089 | 0.315 | 0.567 | 1.953 |
| 3 | 0.125 | 0.494 | 1.500 | 3.421 |
| 4 | 0.162 | 0.641 | 1.429 | 3.216 |
| 5 | 0.192 | 0.946 | 1.811 | 3.636 |

1 vs 5 inside the same 5-member view: domain-facet search narrowed to
one member (`repository_id=`) median **0.911 ms** vs **3.526 ms** all
five — the per-member `search_scoped` fan-out dominates, as designed;
the view (and its identity) still covers the whole workspace. Symbol
query `shared_handler` over all members: **2.302 ms**, 5 items (one per
repo — homonyms never collapse). Graph at 5 members/depth=1: 54 nodes /
136 edges; shared edges are pairwise `shared_entity` cliques over the
5-way `shared_key` join — quadratic in shared entities, bounded by
`limit`/`depth` caps. Search medians have ~±1 ms run-to-run noise on
dev hardware — treat them as tripwires, not SLAs.

**Isolation under stress** (asserted in the benchmark script — it exits
non-zero on violation — and in `TestFiveRepoStress`): repo A re-indexed
to S2 (`mart.grown` added) while a query is pinned to S1 →
`pinned_s2_rows: 0`, `view_id_differs: true`, the latest view does find
S2 (`latest_found_s2: true`). Every search/graph item is tagged with the
snapshot resolved for its own member; nothing from S2 leaks into an
S1-pinned read.

## Immutable views (point-in-time)

`TestImmutableView` proves a persisted pin freezes the view: pin member
A→S1, re-index A (S1→STALE, S2→READY) → `resolve_view` still maps A to
S1 (`resolution="pinned"`, `snapshot_status="STALE"` — aging declared,
not hidden), `view_id` unchanged, S2-only entities invisible and graph
counts (`stats`, `truncated`) honest. Unpinning floats the member to the
new READY and produces a different `view_id`. A member whose snapshots
all fail becomes `snapshot_id=null`/`no-ready-snapshot`: it contributes
zero rows and the federated search reports `member-no-snapshot` with
`members_searched` decremented — absence declared, never fabricated.

## Files

| File | Change |
|---|---|
| `src/code_atlas/federation/model.py` | `compute_view_id` format 2 (full cache-key payload); `VIEW_ID_FORMAT`, `FEDERATION_RESOLVER_ID` moved here |
| `src/code_atlas/federation/federation.py` | `resolve_view` feeds identities + per-member `analyzer_versions` digests + `schema_version` into the key; `_resolve_member` returns the resolved `Snapshot` (no extra query); `_analyzer_digest` helper |
| `src/code_atlas/federation/store.py` | `FederationStore.repo_identities` — batch `identity_key` lookup for members |
| `src/code_atlas/federation/__init__.py` | exports `VIEW_ID_FORMAT` |
| `tests/federation/test_federation_scale.py` | 14 tests: cache-key invalidation matrix, 5-repo stress isolation, immutable-view/diagnostics |
| `benchmarks/bench_federation_scale.py` | CLI-built N-repo workspace + measured scaling + isolation self-check |
| `benchmarks/results/federation-scale-latest.json` | measured payload (this run) |
| `docs/cycle-7-federation.md` | `view_id` description refreshed to point at format 2 |

## Notes / NOT RUN

- No cross-repo data is persisted: `shared_entity` edges remain
  view-derived (`computed=true`), only the v10 workspace tables are
  written (CLI surface only).
- AFTSSS/hardware-in-the-loop measurement: **NOT RUN** — benchmark is
  in-process SQLite on dev hardware; numbers are a regression tripwire,
  not an SLA.
- Explorer/e2e surfaces untouched; `handlers.py` routes unchanged —
  `view_id` flows through the existing payload field.
