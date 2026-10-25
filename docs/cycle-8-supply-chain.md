# Cycle 8 — Supply Chain Audit (Ph.12, lf-0138)

Branch `cycle-8-production-quality`. Ferramentas medidas — nada fabricado;
o que não rodou é declarado `NOT RUN`.

## pip-audit

`.venv/bin/pip-audit` (pip-audit 2.10.1) — **ran**:

```
Found 12 known vulnerabilities in 1 package
Name   Version  IDs
pip    24.0     PYSEC-2026-1795/1796/2875/2876/196/3721
                 (fix versions 25.3 → 26.2.0)
code-atlas-forge  skip: not on PyPI (local package)
```

**Classification**: the only flagged package is **`pip` itself inside the
dev venv** — installer tooling, never shipped nor imported by the product.
Zero vulnerabilities in the 3 runtime deps (`tree-sitter 0.25.2`,
`tree-sitter-python 0.23.6`, `tree-sitter-typescript 0.23.2`) and zero in
the dev toolchain (pytest/ruff/mypy/mcp…). Action: bump the venv's pip is
housekeeping — not a product-dependency fix.

## npm audit (explorer/)

`npm audit` — **ran**: **55 vulnerabilities** (6 low, 20 moderate,
27 high, **2 critical**).

`npm audit --omit=dev` — **ran**: **8 vulnerabilities**
(4 moderate, 4 high) — **all `@angular/*` 18.2.13 runtime packages**.

### Critical findings (2) — dev-only

| Package | Advisory | Reachability |
|---|---|---|
| `piscina ≤4.9.3` | GHSA-x9g3/GHSA-67c8 — prototype-pollution → RCE via `options` | **dev-only**: Angular build-angular worker pool; runs at `ng build` on the maintainer machine, never shipped nor reachable from indexed repo content (the build input is our own TS source, not repo content). |
| `tar ≤7.5.20` | GHSA-34x7/8qq5/83g3/qffp/9ppj/r6q2/vmf3/w8wr — path traversal/overwrite in extraction | **dev-only**: transitive of Angular devkit; no user-controlled tar input in our build pipeline. |

### Angular 18.2.13 — EOL (reachable classification, honest)

`@angular/core`, `compiler`, `common`, `animations` ≤19.2.25 carry **XSS
advisories** (GHSA-58w9/f3m7/jj27/hh8m/prjf/g93w/jrmj/692r/rgjc):
sanitization bypasses in property/namespace/i18n bindings, DOM clobbering
in hydration, response-cache poisoning.

**Verdict: reachable, not dev-only.** The Explorer bundle is served by
`api/server.py` static root and renders repository-derived strings
(symbol names, paths, code snippets) — hostile repo content *is* the
XSS delivery channel into the DOM. Severity high; fix = breaking upgrade
to Angular ≥20 (`npm audit fix --force` → @angular/*@22.2.3, outside the
stated 18.2.x range). This is an `explorer/` source-level migration —
**out of scope for this cycle** (spec: "do not touch explorer/ source";
a bounded fix cannot retarget a major framework). Recorded as the cycle's
top supply-chain item; mitigation exists at the API layer (CSP
`default-src 'self'` + `X-Content-Type-Options` + no-store on every
response) which narrows exploitability but does not remove it.

Also flagged in `npm audit` (dev-only, non-critical): `webpack-dev-middleware`
path traversal (high, dev-server only), `webpack` buildHttp SSRF advisories
(moderate, build-time), `@inquirer/*` (moderate, CLI prompts), `uuid`
(moderate, buffer bounds — dev dep of sockjs), `picomatch`, `postcss`,
`serialize-javascript` — all inside the `devDependencies` closure.

## Pinning audit

| Source | Verdict |
|---|---|
| `pyproject.toml` runtime | **exact-pinned** `==` for all 3 deps ✓ |
| `pyproject.toml` `optional-dependencies.dev` | floor ranges (`>=`) — acceptable: dev-only, resolved fresh; not shipped |
| `pyproject.toml` `optional-dependencies.vector` | `sqlite-vec>=0.1.6` floor — optional, off by default (ADR-0022) |
| `explorer/package.json` dependencies | 16/16 exact-pinned ✓ |
| `explorer/package.json` devDependencies | 11/13 exact; `@axe-core/playwright ^4.13.0`, `@playwright/test ^1.56.1` ranged — dev-only e2e tooling |
| `explorer/package-lock.json` | lockfileVersion 3, **1131 packages, 0 missing `integrity`, 0 non-https `resolved`** ✓ |
| `requirements*.txt` | none exist — pyproject is the single source ✓ |

## License inventory

`pip-licenses`: **NOT INSTALLED** → manual inventory via
`importlib.metadata` over the dev venv (69 distributions).
`license-checker`: **NOT INSTALLED** → `node_modules/*/package.json`
`license` fields read directly for the 16 runtime deps.

- **Permissive**: MIT / Apache-2.0 / BSD-2/3-Clause / MIT-0 / 0BSD /
  PSFL / PSF-2.0 — all runtime deps (`tree-sitter*`: MIT; npm prod:
  `@angular/*` MIT, `graphology*` MIT, `sigma` MIT, `rxjs` Apache-2.0,
  `tslib` 0BSD, `zone.js` MIT) and the whole dev toolchain.
- **Weak copyleft (MPL-2.0)**: `certifi` (via requests), `pathspec`
  (via mypy/pip-audit chain) — **dev-toolchain only**; MPL-2.0 is
  file-level weak copyleft, unmodified use is compliant; neither ships
  in the product wheel.
- **Strong copyleft (GPL/AGPL/LGPL)**: **none found**.

## Dependabot

`.github/dependabot.yml` **before this phase**: `pip` `/` + `github-actions`
`/` — **npm `/explorer/` missing** (real gap — the runtime-impacting
Angular advisories above had no automation coverage). **Fixed this
phase**: added `npm` ecosystem for `/explorer`, weekly, limit 5.

## SBOM

- **Path**: `sbom.json` (repo root) — CycloneDX 1.5, stdlib-generated.
- **Contents**: 1140 components — 24 required, 1 optional, 1115 dev-only
  (npm transitive tree included since package-lock pins it; Python
  transitives not locked — no requirements.lock, stated in `docs/SBOM.md`).
- **Doc**: `docs/SBOM.md` — regeneration notes + scope decisions.

## Findings summary

| # | Item | Status |
|---|---|---|
| S1 | `pip` 24.0 vulns ×12 | dev-venv installer, not shipped — housekeeping |
| S2 | Angular 18.2.13 EOL XSS ×8 (prod-reachable) | **open** — out-of-scope major upgrade; CSP mitigation documented |
| S3 | `piscina`/`tar` criticals | dev-only build tooling — not reachable |
| S4 | Dependabot missing `npm /explorer` | **fixed** this phase |
| S5 | `@axe-core/playwright`, `@playwright/test` ranged | dev-only, accepted |
| S6 | No copyleft deps | verified clean |

## NOT RUN / declared

- `pip-licenses` — not installed → manual `importlib.metadata` inventory.
- `license-checker` (npm) — not installed → licenses read from
  `node_modules/*/package.json` `license` fields (16/16 runtime deps
  measured: MIT ×14, Apache-2.0 ×1, 0BSD ×1).
- `cyclonedx-bom`/`syft` — absent → stdlib SBOM per spec's fallback.
- No `requirements*.txt` — `pip list` used for the env inventory.
