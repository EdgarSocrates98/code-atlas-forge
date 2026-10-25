# Accessibility — GA (cycle-8.1, lf-0150)

Detail docs: `docs/cycle-8-a11y.md` (§cycle-8.1 re-verification),
`docs/explorer-accessibility-report.md`. Commit: `e2c00f1`.

## Measured on Angular 22.2.3

| gate | result |
|---|---|
| `ng test` | 238/238 |
| `playwright e2e/a11y.spec.ts` | **66/66** — 48 axe scans, **0 violations** |
| `playwright e2e/screenshots.spec.ts` | **65/65** — all PNGs assert overflow ≤ 1px |
| Lighthouse 13.5.0 | **Accessibility 100 + best-practices 100** on `/dashboard`, `/search`, `/graph`, `/settings` |

## Post-migration delta

Zero new axe violations vs. Angular 18 baseline; zero `src/` fixes
needed. Focus rings intact (native elements + global `:focus-visible`
rule — no CDK dependency). Zoom 200% + reduced-motion re-verified on
all populated domain views. New test pins focus-ring visibility on
`/search` input and `/graph` toolbar.

## Honest caveats

- axe + keyboard coverage only — **no screen-reader run** (VoiceOver/
  NVDA NOT RUN, declared in scorecard).
- Lighthouse performance 83, SEO 91 (robots.txt N/A for loopback),
  agentic-browsing 50 (new LH13 informational category).
- Concurrent Playwright runs on the same port wipe each other's trace
  artifacts — use `ATLAS_E2E_PORT` + `--output` for isolation
  (follow-up spec candidate).
