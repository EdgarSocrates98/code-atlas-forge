# Browser compatibility (cycle-8.1, lf-0149)

Commit: `0c0b980` (swept files: `413265a`, `60f8cb8`).

## Coverage

New `explorer/e2e/cross-browser.spec.ts` (3 smoke journeys) + opt-in
`firefox`/`webkit` Playwright projects gated behind
`ATLAS_E2E_XBROWSER=1`. CI contract preserved — default e2e stays
Chromium-only; cross-browser is an explicit opt-in run.

## Measured

**9/9 passed (12.7s)** across Chromium 1248, Firefox 1555,
WebKit 2370 (Playwright 1.64.1) — zero console errors on all engines.

## Finding caught by this spec

Stale packaged `explorer_dist` shadowed a fresher dev dist → Beasties
`onload=` CSP violation on every page, every engine. Root fix landed
in lf-0147 step-4 (`inlineCritical: false`); precedence hazard in
`explorer_static_root()` documented in
`docs/explorer-wheel-packaging.md`.

## NOT RUN

Safari (native, non-WebKit-playwright) and older browser baselines —
declared, not claimed.
