# Cycle 8.1 — baseline & evidence index

Spec: `lf-0146-cycle81-baseline.md`.

## Baseline (branch start, post-cycle-8 merge `d5d620a`)

- `pytest`: **1511 passed, 1 skipped**
- ruff/mypy strict: clean · wheel build: ok · `ng test`: 219 ·
  `npm run build`: ok
- `npm audit --omit=dev`: **8 vulns** (Angular 18.2.13 EOL) → fixed to
  **0** by lf-0147.

## Final state (all gates, this branch)

| surface | result | evidence |
|---|---|---|
| pytest | 1658 passed, 1 skipped | `docs/cycle-8.1-platform-homologation.md` |
| release_check `--skip-e2e` | 13 PASS / 0 FAIL / 1 SKIP | idem §release gates |
| `ng test` | 238/238 | `docs/angular-migration.md` |
| Playwright full | 179/179 + journeys 8/8 + xbrowser 9/9 | `docs/angular-migration.md`, `docs/cycle-8.1-journeys.md`, `docs/browser-compatibility.md` |
| axe | 48 scans, 0 violations; Lighthouse a11y/bp 100 | `docs/accessibility-ga.md` |
| pip-audit prod / npm audit prod | 0 / 0 | `docs/supply-chain-ga.md`, `docs/security-ga-review.md` |
| SBOM | 564 components, deterministic, freshness-tested | `docs/supply-chain-ga.md` |
| wheel+sdist | byte-identical reproducible | `docs/explorer-wheel-packaging.md` |
| security findings | 13 total fixed (7 c8 re-verified + 6 new) | `docs/security-ga-review.md` |
| migrations | fail-safe + chain v1→v12 lossless | `docs/impact-truncation-integrity.md` |
| public surface | 63 contract tests pin CLI/MCP/API | idem |

## Per-spec evidence docs

| spec | doc |
|---|---|
| lf-0147 Angular+CSP | `docs/angular-migration.md` |
| lf-0148 wheel packaging | `docs/explorer-wheel-packaging.md` |
| lf-0149 platform+browser | `docs/cycle-8.1-platform-homologation.md`, `docs/cross-platform-ga.md`, `docs/browser-compatibility.md` |
| lf-0150 a11y | `docs/accessibility-ga.md` |
| lf-0151 impact truncation | `docs/impact-truncation-integrity.md` |
| lf-0152 graph density | `docs/graph-density-ga.md` |
| lf-0153 security final | `docs/cycle-8.1-security-final.md`, `docs/security-ga-review.md` |
| lf-0154 deps/SBOM | `docs/cycle-8.1-deps-sbom.md`, `docs/supply-chain-ga.md` |
| lf-0155 compat contracts | `docs/impact-truncation-integrity.md` (manifest section) |
| lf-0156 e2e journeys | `docs/cycle-8.1-journeys.md` |
| lf-0157 release gates | `docs/cycle-8.1-platform-homologation.md` §gates |
| lf-0158 trusted publish | `docs/release-pipeline-oidc.md` |
| lf-0159 versioning | `docs/RELEASING.md` |
| lf-0160 docs/DX | this file + README/CHANGELOG |
| lf-0161 delivery | `docs/ga-readiness-scorecard.md` |
