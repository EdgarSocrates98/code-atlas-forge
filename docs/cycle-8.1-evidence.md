# Cycle 8.1 — evidence register

Every claim in this cycle maps to a committed artifact or a verbatim
command output captured in a spec doc. Nothing is asserted without a
pointer.

## Verbatim-run docs (agent-authored, reviewed)

- `docs/cycle-8.1-platform-homologation.md` — pytest/release_check/CLI/
  API verbatim tables, Windows CI legs, browser matrix
- `docs/cycle-8.1-security-final.md` — 6 findings, reproduction
  vectors, fix locations, 147-test inventory
- `docs/cycle-8.1-deps-sbom.md` — audit outputs, license table,
  dependabot matrix
- `docs/cycle-8.1-journeys.md` — 8 playwright journeys + CLI journey,
  findings A1–A4
- `docs/cycle-8.1-graph-density.md` — ceiling/heap/long-session raw
  numbers
- `docs/cycle-8-a11y.md` §8.1 + `docs/explorer-accessibility-report.md`
  — axe/Lighthouse verbatim post-migration

## Machine-checkable artifacts

- `benchmarks/results/graph-*.json` — raw benchmark payloads
- `sbom.json` — deterministic CycloneDX 1.5 (regenerable,
  freshness-tested)
- `tests/packaging/test_sbom_freshness.py`,
  `tests/security/test_cycle81_security_final.py` (62),
  `tests/e2e_journeys/` — regression pins for this cycle's claims

## Gates of record

`scripts/release_check.sh` final run on this branch — see
`docs/ga-readiness-scorecard.md` for the summary table and
`docs/cycle-8.1-baseline.md` for the per-gate mapping.
