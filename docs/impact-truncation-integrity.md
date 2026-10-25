# Impact truncation integrity — lf-0151 (Cycle 8.1 Phase 8)

Measured gap in the Cycle 8 scorecard (`docs/cycle-8-scorecard.md` §4,
`docs/cycle-8-impact-validation.md` → “Limites declarados”):

> `max_nodes`/`max_depth` no impact truncam **sem** levantar
> `truncated` — só `max_paths` marcava o flag.

This document defines the contract implemented to close that gap.
Rule: **every bound that discards real work declares `truncated` plus a
named reason**. A generic TRUNCATED without a reason would be inverted
silence — the report must name which bound cut the analysis.

## Truncation reasons

`ImpactResult.truncation_reasons: tuple[str, ...]` (additive, schema
v1) carries a sorted set of reason codes. `truncated` is exactly
`bool(truncation_reasons)` — the boolean never diverges from the
reason set.

| Reason | Bound | Where it fires |
|---|---|---|
| `max-nodes-edge-scan` | `ImpactLimits.max_nodes` | A per-node expansion read the full `LIMIT max_nodes` prefix of `ORDER BY id` rows in `relations`/`domain_relations` — rows beyond the deterministic prefix were never inspected. |
| `max-depth-frontier` | `ImpactLimits.max_depth` | A node queued at the `max_depth` frontier still had traversable work (resolved neighbour or never-counted `to_unresolved` dependency) that was never expanded. |
| `max-paths` | `ImpactLimits.max_paths` | Emission stopped at the candidate cap (pre-existing behaviour). |
| `max-changes` | `ImpactLimits.max_changes` | The diff was capped before the blast ran (pre-existing behaviour, now reasoned). |
| `symbol-seed-limit` | `_SYMBOL_SEED_LIMIT = 64` | Wide-`LIKE` symbol seeding exceeded the seed cap — some changed symbols never entered the blast. |
| `test-candidates` | `MAX_TEST_CANDIDATES = 50` | Test-candidate output reached the cap (pre-existing constant, now reasoned). |

`max_edges` and `query_budget` do **not** exist as independent Impact
Engine bounds — they are not represented as reasons rather than
invented.

## Deterministic cut semantics

Per-node edge scans read a deterministic prefix:
`ORDER BY id LIMIT max_nodes`. A truncated result is therefore a
**prefix** of the unbounded result, never an arbitrary subset — two
runs at the same cap emit identical candidate sequences
(`test_truncated_prefix_is_deterministic`). When the cap cuts
mid-edge-type ordering, the surviving rows are exactly the first
`max_nodes` rows in `id` order; nothing about the ordering changes.

The `max_depth` frontier probe (`_frontier_pending`) inspects nodes
queued at exactly `hops == max_depth`: if any has a remaining
traversable edge (resolved neighbour or never-counted
`to_unresolved`), the cut is declared. A frontier with no remaining
work stays honestly `truncated=False` — exact-fit and clean-frontier
cases are tested, no false positives by convention.

## Where honesty surfaces

- `blast_radius.blast` keeps its public `(candidates, bool)` contract;
  code-path reasons propagate through `domain_traversal`/`engine`.
- `domain_traversal.BlastOutcome.reasons` exposes the per-reason set
  for the mixed symbol+domain BFS.
- `ImpactResult.truncated` = union of reasons; `truncation_reasons`
  serialises in `to_json` (additive field, old readers unaffected),
  `to_markdown` renders
  `**TRUNCATED**: limites de análise atingidos (reason, …)` and the
  human CLI output prints the same reason list — JSON, Markdown and
  CLI are parity surfaces.
- `eval/impact_truth.truncation_check` distinguishes emission reasons
  (`max-paths`) from non-emission ones: an over-cap `--max-paths`
  probe must not clear `max-nodes-edge-scan`/`max-depth-frontier`,
  and a loose probe must show no `max-paths` reason.

## Measured evidence (2026-10-25)

- `tests/impact/test_truncation_integrity.py`: 15 tests — code blast
  edge-scan cap declared, exact-fit `+1` probe clean, depth frontier
  declared, clean frontier honest `False`, domain equivalents with
  discriminated reasons, mixed-reason union, engine envelope E2E,
  deterministic prefix.
- Scoped run: `pytest tests/impact tests/contracts tests/storage
  tests/evaluation/test_impact_truth.py tests/search/test_domain_fts.py`
  → **237 passed**.
