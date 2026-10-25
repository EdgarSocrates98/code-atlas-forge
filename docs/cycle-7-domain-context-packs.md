# Cycle 7 — Domain context packs (lf-0121, Phase 13)

Status: implemented on `cycle-7-domain-intelligence-federation`. The
builder is **read-only over persisted snapshot state** — it never
re-reads artifacts, never executes code/jobs/Terraform/pipelines, and
never contacts external systems. Every read is anchored on
`snapshot_id` + `repository_id`: snapshot isolation between
repositories is preserved by construction.

A domain pack answers "give me bounded context about *this entity*"
for any `DomainEntityKind` seed — `data_table`, `spark_job`,
`terraform_resource`, `api_endpoint`, etc. It is a normal
`ContextPack` (envelope **schema v2**) carrying the additive `domain`
section (`schema_version = 1`); v2 consumers ignore the extra key.

## Layout

| File | Role |
|---|---|
| `src/code_atlas/context/domain.py` | `DomainContextEngine(ContextEngine)` — `build_domain(DomainPackRequest)`; seed resolution, `domain_relations` neighbors, evidence, diagnostics; `resolve_domain_ref` (the backing fn for the future `/domains/entities` route); `DOMAIN_REQUERY_CONTRACT` |
| `src/code_atlas/context/contracts.py` | `DomainPackRequest`, `DomainEntityCard`, `DomainNeighborRef`, `DomainPackSection`, `DomainPackPart`, `DomainSectionFactory`, `ContextPack.domain` (additive), `DOMAIN_PACK_SCHEMA_VERSION = 1` |
| `src/code_atlas/context/engine.py` | `domain_factory` kwarg threaded through `_enforce_payload_budget` → `_pack` so the section is rebuilt inside every budget iteration (its cost is counted in the payload estimate) |
| `src/code_atlas/domains/summarize.py` | `summarize_lineage(subject, facts)` — deterministic, template-only lineage text (`LineageFact`); no LLM phrasing |
| `src/code_atlas/context/renderers.py` | `render_markdown` gains a `## Domain — <status>` block when the section is present (JSON renderer needs no change: `to_dict` covers it) |
| `tests/context/test_domain_packs.py` | 10 tests: seeded fixture via `persist_domain_result` + `CrossDomainResolver`, budget honesty, ref resolution, absent data, seed states, symbol bridge, determinism |

## Seed forms (`DomainPackRequest.seed`)

- `dom:<identity_key>` — canonical logical key, repo-scoped;
- `entity:<row_id>` or bare digits — `domain_entities` row id **of
  that repository** (cross-repo ids are rejected by the
  `repository_id` predicate);
- `<qualified_name>` — exact match within the repo; >1 match →
  `seed-ambiguous` with the candidate `identity_key`s, never a lucky
  pick.

`max_neighbors` (default 20, cap 100) bounds the top-N neighbor cut by
confidence.

## Section semantics

`domain.status` is the honest word of the build: `ok`,
`absent` (snapshot has zero domain occurrences), `seed-not-found`,
`seed-ambiguous`, `seed-not-in-snapshot`.

`counts` aggregates **every** `domain_relations` edge touching the
seed, not just delivered neighbors: `incoming`/`outgoing`, per-status
(`resolved`/`ambiguous`/`external`), `unresolved` excluding honesty
markers, and `dynamic` for `dynamic:`/`${dynamic}`/`environment:`
targets (adapter markers — counted separately, never treated as
pending resolutions). `unresolved_targets`/`ambiguous_targets` are
samples (cap 10); pending texts also surface in the envelope's
`unresolved_refs`.

Neighbors are deduplicated per ref and ranked top-N by confidence
(`confidence_rank` desc → better status → first relation id → ref).
Entity neighbors carry `dom:` identity keys; code symbols reached via
`*_symbol_occ_id` endpoints carry `sym:<qualified_name>` — the
domain↔code bridge.

`section.neighbors` mirrors **exactly** the delivered items: the
`domain_factory` is re-invoked on each enforcement iteration, so a
neighbor dumped by budget never appears in the section (it degrades to
an `expansion_ref` — `neighbors_omitted = total - delivered`).

## Expansion refs — the re-query contract

`expansion_refs[].ref_id` is a resolvable ref, and the contract is
documented *inside the pack* (`domain.requery`):

- `dom:<identity_key>` → `GET /domains/entities?identity_key=<ref>
  &repository_root=<root>&snapshot_id=<sid>`;
- `entity:<row_id>` → same route by row id;
- `sym:<qualified_name>` → code-graph symbol (structural search /
  `ContextExpander`), not the domain route.

`resolve_domain_ref(conn, repository_id, snapshot_id, ref)` is the
engine-side backing function — tests call it directly; it returns
`None` for malformed refs, cross-repo entities, or entities without an
occurrence in the snapshot.

## Wiring point (deferred, per scope guard)

The HTTP/CLI surface is intentionally **not** wired here (lf-0123 owns
CLI/MCP/API domain commands). The integration point, when it lands:

```python
# POST /context/domain  (api/handlers.py — DO NOT wire in this spec)
pack = DomainContextEngine(conn).build_domain(DomainPackRequest(
    seed=..., repository_root=..., snapshot_id=..., level=...,
    budget_tokens=min(budget, 64000), max_neighbors=...))
return pack.to_dict()

# GET /domains/entities?identity_key=…&repository_root=…&snapshot_id=…
card = resolve_domain_ref(conn, repo_id, snapshot_id, ref)
```

`explorer/` views can consume `pack.to_dict()["domain"]` directly —
the section is plain JSON.

## Honesty invariants

- Evidence-first: items carry `domain-relation`/`domain-occurrence`/
  `evidence` refs pointing at persisted rows; nothing is invented.
- `tokens_estimated=True`; the domain section is built inside the
  payload-budget loop so its serialization is estimated too.
- `inferred` relations are never promoted — the pack reports
  `confidence_class` verbatim from the store.
- Self-edges count in `counts` but an entity is never its own
  neighbor.
