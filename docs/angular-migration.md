# Angular migration — 18.2.13 → 22.2.3 (cycle-8.1, lf-0147)

Spec: `lf-0147-cycle81-angular-migration.md`. Commits: `b0e5bb2`,
`2126fb2`, `413265a`, `eacf06a` (4 sequential `ng update` steps) +
evidence regen `c9f5fce`.

## Why

Angular 18.2.13 was EOL and carried 8 advisories (4 moderate, 4 high)
in `npm audit --omit=dev` — the #1 GA blocker on the cycle-8
scorecard.

## Path

Staged migration through each major line — 18→19→20→21→22.2.3 — one
commit per step so any bisectable regression has a single boundary.

## Result (measured)

| gate | before | after |
|---|---|---|
| `npm audit --omit=dev` | 8 vulns (4 mod, 4 high) | **0** |
| `npm ci` | — | clean |
| `npm run build` | ok | ok, ~888K, budgets pass |
| `ng test` | 219 | **238/238** |
| Playwright e2e | 179 | **179/179** (9 spec files) |

## Changes

- `@angular/*` 18.2.13 → 22.2.3; TypeScript 5.5.4 → 6.0.3;
  zone.js 0.14.10 → 0.15.1.
- Builders migrated to `@angular/build`.
- Removed unused deprecated packages: `@angular/animations`,
  `@angular/platform-browser-dynamic`.
- Karma config modernized.
- Signal-effect tests: Angular 19+ no longer flushes effects through
  `fixture.whenStable()` alone → `TestBed.tick()` + extra stability
  wait in affected specs.
- Production `optimization.styles.inlineCritical = false` — Beasties
  was emitting an inline `onload=` script, incompatible with strict
  `script-src 'self'` CSP.

## CSP hardening (same spec)

`src/code_atlas/api/server.py`:

- removed `script-src-attr 'unsafe-inline'`;
- added `worker-src 'self'`, `object-src 'none'`, `base-uri 'self'`,
  `form-action 'self'`;
- kept `style-src 'unsafe-inline'` — Angular runtime style bindings
  require it (verified against real `explorer_dist/index.html`);
- source audit: no `innerHTML`/`outerHTML`/`bypassSecurityTrust*`/
  `document.write`/`srcdoc`/`eval`/dangerous SVG-MathML/unsafe src
  bindings in Explorer sources.

## Residual

`npm audit` **with** dev deps still reports 2 dev-only Vite advisories
(build-time tooling only, never shipped in the wheel); runtime audit
is clean. Tracked — upgrades land with the next Vite/Angular toolchain
bump.
