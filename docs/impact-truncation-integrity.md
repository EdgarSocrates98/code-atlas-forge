# Impact truncation integrity (cycle-8.1, lf-0151)

Spec: `lf-0151-cycle81-impact-truncation.md`. Commit: `d7a906f`
(with lf-0157 compat/migrations).

## Contract

`ImpactResult.truncation_reasons` — every truncation is **explicit and
integrity-preserving**: the result never silently drops frontier, and
an exact-fit at a limit is distinguishable from real overflow.

## Mechanism

- `+1` probes at node/depth limits → exact-fit vs. actual overflow is
  detected, not assumed.
- Reasons propagate from each stage: `symbol-seed-limit`,
  `max-changes`, `test-candidates`, domain/code frontier truncation.
- Markdown render, CLI output, JSON payload, and eval validation all
  agree on truncation state — one source of truth, four projections.

## SQLite migration integrity (same commit)

- Unknown/future migration versions raise `MigrationError` (was:
  silently accepted).
- All applied migrations checksum-validated on open.
- FTS-gated migrations correctly skipped on non-FTS5 builds.
- Chain tests run real v1 databases through lossless upgrade to v12.

## Public surface manifest

63 tests pin the stable public surface: 19 CLI subcommands, 15 MCP
tools, all API GET/POST routes, version surfaces — contract drift
fails loudly instead of shipping silently.
