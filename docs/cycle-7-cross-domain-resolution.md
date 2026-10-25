# Cycle 7 — Cross-domain resolution (lf-0117, Phase 9)

Status: implemented on `cycle-7-domain-intelligence-federation`. The
resolver is **opt-in, stateless, and read-only over persisted snapshot
state** — it never re-reads artifacts, never executes code/jobs/
Terraform/pipelines, and never contacts external systems.

The layer connects entities observed by **different adapters** inside
one snapshot: the table SQL declares and PySpark reads, the
`aws_glue_job` Terraform provisions and the script the job runs, the
`fetch("/v1/pets")` literal and the `GET /v1/pets` of the OpenAPI
contract, the artifact a CI workflow publishes and the file present in
the repository.

## Layout

| File | Role |
|---|---|
| `src/code_atlas/domains/crossresolve.py` | `CrossDomainResolver.plan(store, snapshot_id) → CrossResolutionPlan`; agreement rules, evidence lookup, diagnostics |
| `src/code_atlas/storage/store.py` | Snapshot readers (`domain_occurrences_snapshot`, `domain_relations_snapshot`, `evidence_snapshot`, `file_symbol_occurrences`) + `persist_cross_resolutions` + `cross_snapshot_domain_relations` |
| `src/code_atlas/indexing/coordinator.py` | Opt-in `cross_resolver` parameter; `_run_cross_resolution` inside the publication transaction; version key `resolver:cross-domain`; cross-snapshot domain audit in `_validate` |
| `tests/domains/test_crossresolve.py` | 14 fixture tests: every matching rule, snapshot isolation, opt-in integration, idempotence |

## The one rule that matters

**Never match on bare name.** A link requires qualified-identity
agreement:

- `catalog`/`namespace`/`environment`/`root` + `qualified_name` for
  data and cloud entities;
- account+region+resource for `arn:aws:…` targets;
- literal `method+path` equality for consumer ↔ contract endpoints;
- literal repo-relative path for artifact/script anchors.

Same name with divergent qualifier is exactly what `DomainIdentity`
exists to separate — it becomes `ambiguous` (all candidates preserved
in `to_unresolved`), never `resolved`.

## Resolution states

| Situation | Result |
|---|---|
| Single full qualified match | `resolved`, `confidence_class=resolved` |
| ≥2 full matches, or partial agreement (name+ns but catalog/env diverges) | `ambiguous`, candidates kept in `to_unresolved` — schema has no candidates column, so the text is the honest record |
| Absolute URL, ARN with no local entity | `external` |
| Bare name, no qualifier | stays `unresolved` + `unqualified-target` diagnostic — never an edge |
| Qualified target with no cross-domain entity in snapshot | `unresolved` + `no-cross-target` diagnostic |
| Candidate lacking locatable source evidence | `unresolved` + `missing-evidence` diagnostic |
| Same-domain target (e.g. sql→sql) | not touched — intra-domain resolution is the adapter's own job |
| `dynamic:`/`environment:`/marker targets | skipped entirely — they are honesty markers, not pending resolutions |

Two mechanisms:

- **Updates** — adapter-emitted `unresolved` rows are re-resolved in
  place (`UPDATE … WHERE id=? AND snapshot_id=?`), gaining the target
  endpoint and merged evidence from **both** sides.
- **Edges** — entity↔entity rows are created for full qualified-identity
  pairs (`REFERENCES`) and attribute-path anchors (`PROVISIONS` when the
  anchor-holding entity is Terraform, `REFERENCES` otherwise; an anchor
  with no entity falls back to the file's `FILE` symbol occurrence —
  the domain→code bridge). Every edge carries evidence ids from both
  sides or is not emitted.

Ambiguous *edges* are also persisted: two same-named entities from
different adapters with partial qualifier agreement produce a
`REFERENCES ambiguous` row whose `to_unresolved` lists both candidate
identities — the honest counterpart of the `ambiguous` update.

## Evidence rule

`resolver='cross-domain'` on every touched row. `evidence_ids` always
unions the relation's own evidence with the target occurrence's source
evidence (`extractor='domain:<id>'` at the occurrence's
`source_file_id`, preferring span-precise rows). An edge missing
either side's evidence is dropped with a diagnostic — evidence-first
is not negotiable.

## Indexing enablement (opt-in)

```python
from code_atlas.domains import CrossDomainResolver, DomainRegistry
from code_atlas.indexing import IndexCoordinator

coordinator = IndexCoordinator(
    conn,
    domain_registry=registry,          # enabled domain adapters
    cross_resolver=CrossDomainResolver(),
)
```

- `cross_resolver=None` (default) — byte-for-byte prior behavior.
- Runs **after all adapters persist**, inside the same
  `BEGIN IMMEDIATE` publication transaction — a failure rolls back the
  whole snapshot. The plan reads the just-written, uncommitted rows via
  the shared connection.
- `resolver_version` enters `analyzer_versions` as
  `resolver:cross-domain` → enabling/disabling/upgrading defeats the
  no-op path.
- Stats land in `IndexOutcome.stats` (`cross_considered`,
  `cross_resolved`, `cross_ambiguous`, `cross_external`, `cross_edges`,
  `cross_diagnostics`, `cross_missing_evidence`, `cross_updated`,
  `cross_inserted`); `warning`/`error` diagnostics join the job
  `failures` list as `cross:<code>:<message>`.
- `_validate` audits `cross_snapshot_domain_relations(snapshot_id)` —
  domain edges whose endpoints escape the snapshot abort publication
  (integrity error, rollback).
- Without a `domain_registry` the plan is legitimately empty — no rows
  of domain data exist to link.

## Idempotence

Rows already bearing `resolver='cross-domain'` are skipped; existing
`from→to` entity pairs seed the edge dedup set; update targets also
dedup new edges. A second `plan` over resolved state converges to
empty (tested).

## Known limits (declared, not hidden)

- Matching is conservative by design: qualified names that don't share
  the positional `catalog.namespace.name` shape, and Terraform resources
  referenced by non-path attributes (e.g. an ARN embedded in a computed
  string), stay unresolved rather than guessed.
- `ambiguous` rows record candidates inside `to_unresolved` text (the
  schema has no candidates column) — parseable but not relational.
- The `FILE`-symbol fallback for path anchors requires the artifact file
  to have *some* evidence row in the snapshot; an inventoried file with
  zero evidence yields a diagnostic, not an edge.
- `domains/{spark,aws}` are in-flight WIP on this branch and were not
  touched; the resolver is domain-agnostic (identity + path + ARN +
  endpoint rules) and needs no adapter-specific code for them.
