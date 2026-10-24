# Graph Report - code-atlas-forge  (2026-10-24)

## Corpus Check
- 36 files · ~12,418 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 17 nodes · 14 edges · 3 communities
- Extraction: 100% EXTRACTED · 0% INFERRED · 0% AMBIGUOUS
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `aaa56c70`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Review: lf-0001 — Cycle 1 scaffold
- 2026/lf-0001.md
- lf-0001.review.claude.md

## God Nodes (most connected - your core abstractions)
1. `Review: lf-0001 — Cycle 1 scaffold` - 5 edges
2. `Context` - 1 edges
3. `Acceptance Criteria` - 1 edges
4. `Constraints` - 1 edges
5. `Review Notes` - 1 edges
6. `Critérios de aceitação` - 1 edges
7. `Verificação executada` - 1 edges
8. `Findings` - 1 edges
9. `Resultado` - 1 edges
10. `Grill Gate` - 1 edges

## Surprising Connections (you probably didn't know these)
- None detected - all connections are within the same source files.

## Communities (3 total, 0 thin omitted)

### Community 0 - "Review: lf-0001 — Cycle 1 scaffold"
Cohesion: 0.33
Nodes (5): Critérios de aceitação, Findings, Resultado, Review: lf-0001 — Cycle 1 scaffold, Verificação executada

### Community 1 - "2026/lf-0001.md"
Cohesion: 0.33
Nodes (5): Acceptance Criteria, Constraints, Context, Grill Gate, Review Notes

### Community 2 - "lf-0001.review.claude.md"
Cohesion: 0.40
Nodes (4): Acceptance Criteria, Constraints, Context, Review Notes

## Knowledge Gaps
- **13 isolated node(s):** `Context`, `Acceptance Criteria`, `Constraints`, `Review Notes`, `Critérios de aceitação` (+8 more)
  These have ≤1 connection - possible missing edges or undocumented components.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What connects `Context`, `Acceptance Criteria`, `Constraints` to the rest of the system?**
  _13 weakly-connected nodes found - possible documentation gaps or missing edges._