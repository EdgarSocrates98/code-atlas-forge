# Supply chain — GA (cycle-8.1, lf-0154)

Detail doc: `docs/cycle-8.1-deps-sbom.md`. Commit: `ddbd098`.

## SBOM

`scripts/generate_sbom.py` (stdlib-only) emits deterministic
CycloneDX 1.5 — UUID5 serial, no timestamps, byte-identical re-runs.
`sbom.json`: **564 components** (1140→564 after the Angular 22
toolchain slim-down + nested `node_modules` dedup): 24 required,
1 optional, 539 excluded (dev). Freshness enforced by
`tests/packaging/test_sbom_freshness.py` (6 tests) — a stale BOM
fails CI.

## License gate: PASS

24/24 production deps permissive — MIT×21, Apache-2.0×2, 0BSD×1.
**Zero copyleft.** Optional `sqlite-vec` (not installed) verified
MIT/Apache-2.0 on PyPI.

## Vulnerability state

- `pip-audit` on pinned prod requirements: **0**.
- `npm audit --omit=dev`: **0** (Angular 22.2.3).
- Dev-only: 2 Vite advisories (build tooling, never shipped);
  venv's own pip upgraded 24.0→26.2.2 (12 toolchain CVEs closed).
- Python transitive deps are not itemized in the BOM (no
  requirements.lock — documented limitation; pip-audit covers the
  installed env as superset).

## Dependabot

Covered and locked by test: pip `/`, github-actions `/`, npm
`/explorer` — all weekly.
