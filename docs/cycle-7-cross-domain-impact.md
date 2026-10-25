# Cycle 7 — Cross-domain impact (lf-0120, Phase 12)

Status: implemented on `cycle-7-domain-intelligence-federation`.

The Impact Engine now answers the domain question — *"if this
table/job/resource changes, who is affected?"* — by running a **mixed
blast radius**: code edges (`relations`, dependent in `from`) plus
domain edges (`domain_relations`, entity-or-symbol endpoints) in a
single BFS. Everything stays **inside one snapshot** — the
repository+snapshot isolation of the rest of the engine is
non-negotiable here too.

No jobs, SQL, Spark, Terraform, or pipelines are ever executed; the
traversal reads only persisted, snapshot-scoped extraction state.

## Layout

| File | Role |
|---|---|
| `src/code_atlas/impact/domain_traversal.py` | Mixed-graph BFS (`blast`), entity-seed resolution (`resolve_seeds` / `resolve_entity_refs`), domain-coverage probe (`domain_state`) |
| `src/code_atlas/impact/engine.py` | `_blast` step wires seeds + mixed traversal + reconcile + `unknown_coverage` flags; `_reconcile` classifies domain candidates by `identity_key` |
| `src/code_atlas/impact/contracts.py` | `ImpactRequest.entity` seed field; `ImpactCandidate.node_type`/`domain_occurrence_id`/`uncertain_deps`; `ImpactPath.edge_refs` |
| `src/code_atlas/impact/report.py`, `packs.py` | JSON carries the new fields; Markdown/pack lines mark `domain` nodes and per-hop edge provenance |
| `tests/impact/test_domain_impact.py` | 8 tests on a sql+pyspark index: downstream blast, honest statuses, ambiguous/unresolved uncertainty, disabled-adapter flag, snapshot guard |

## Direction semantics

A domain edge propagates change only along the direction of the
dependant — never blindly in both directions:

- **`from` depends on `to`** — `READS_FROM`, `DERIVES_FROM`,
  `DEPENDS_ON`, `CALLS_ENDPOINT`, `IMPLEMENTS_CONTRACT`,
  `USES_RESOURCE`, `ASSUMES_ROLE`, `TESTS`, `REFERENCES`
  (non-identity), `IMPORTS`, `CALLS`, `IMPLEMENTS`, `EXTENDS`,
  `CONFIGURES`, `CONTAINS`: traverse `to → from` (a change in the
  target impacts the consumer/aggregate).
- **Coupled** — `WRITES_TO`, `TRANSFORMS`, `PROVISIONS`,
  `TRIGGERS`, `DEPLOYS`, `DEFINES`: traverse **both** directions.
  The product is affected by its producer (writer change → table
  content), and the producer depends on the product's state (writing
  into a table whose schema changed breaks; drifted resource forces
  a change to the Terraform that declares it).
- **Symmetric identity** — `REFERENCES` entity↔entity whose
  `identity_key` positional segments agree from `root` onward
  (the cross-domain pair emitted by `crossresolve`): both
  directions; it is the *same* logical thing seen by two adapters.

This is why a `staging.orders` seed reaches the `spark_job` that
contains the reading transformation and the downstream
`mart.enriched_orders`, while seeding `staging.enriched` reaches the
transformation that writes it (coupled `WRITES_TO`) but **not** its
source tables — `DERIVES_FROM` stays directional, and a derived
table changing does not affect its inputs.

## Honest states, no invented relations

- Every hop carries `resolution_status` into
  `ImpactPath.resolution_statuses`. `fully_verified` requires all hops
  `resolved`; an `ambiguous`/`unresolved`/`external` hop demotes the
  candidate to `possible` — never promotes it.
- Rows whose `to` has no concrete endpoint (`to_unresolved`) are
  **uncertainty of the node**, never a blast hop: they land in
  `ImpactCandidate.uncertain_deps` (e.g.
  `DERIVES_FROM→src (unresolved)`) and in the aggregated
  `unknown_coverage` counters `domain-<status>-deps:N`.
- Per-hop provenance is typed: `ImpactPath.edge_refs` holds
  `rel:<id>` for `relations` and `dom:<id>` for `domain_relations`
  (the two id spaces overlap). `relation_ids` keeps only code-edge
  ids for backwards compatibility.
- Endpoints whose occurrence lives in **another snapshot** are never
  traversed — they are counted in `BlastOutcome.cross_snapshot` and
  surfaced as `domain-cross-snapshot:N` in `unknown_coverage`. This is
  the read-time federation guard; indexing-time validation already
  rejects such rows.
- Domain coverage flags in `unknown_coverage` distinguish
  `domain-adapters-disabled` (no domain adapter ran — configuration,
  not failure) from `domain-data-empty` (adapters ran, nothing
  extracted) from silently absent data.

## Seeds

`ImpactRequest.entity` is the explicit channel. Accepted forms
(`resolve_entity_refs`):

| Form | Example |
|---|---|
| canonical `identity_key` | `dom:pyspark/<enc-root>///staging//orders` |
| `domain_entities` row id | `entity:12`, `id:12`, `12` |
| `DomainIdentity.display()` | `pyspark:staging.orders` |
| qualified name | `staging.orders`, `orders` |

Multiple matches seed **all** of them (`entity-seed-multi`
diagnostic — honest multiplicity); no match yields
`entity-seed-not-found`; an entity with no occurrence in the
analysed snapshot pair yields `entity-seed-no-occurrence`.
Seeds resolve per-snapshot: target occurrences seed the target
blast, base occurrences seed the base blast (removed-symbol
reconciliation preserved).

## Surface wiring (handlers untouched)

API/CLI/MCP handlers were intentionally **not** modified — the
engine already accepts entity seeds through the existing
`ImpactRequest.symbol` field: a symbol ref that matches a domain
entity in any of the forms above is also seeded (silently — the
field keeps its primary symbol-filter role). Therefore the existing
surfaces work today without signature changes:

- API `POST /impact` — pass the entity ref in `symbol`;
- CLI `code-atlas impact --ident <ref>` — pass the entity ref;
- MCP `atlas_impact` — pass the entity ref in `symbol`/`ident`.

To expose `entity` explicitly on those surfaces it is enough to
forward a homonymous parameter into `ImpactRequest` — the engine
reads it already. That wiring is deliberately left to the handler
owner.

## Envelope

Impacted items report: `node_type` (`symbol` | `domain`), entity
kind in `kind` (`spark_job`, `data_table`, …), qualified identity in
`qualified_name` (`pyspark:staging.orders` display form), canonical
key in `symbol_key` (`dom:…` identity), `path.hops`,
`path.resolution_statuses`, `path.edge_refs`,
`domain_occurrence_id`, `uncertain_deps`, plus the usual
`strength`/`impact` axes. `ImpactResult.truncated` stays explicit
when `max_depth`/`max_nodes`/`max_paths` cut the BFS.
