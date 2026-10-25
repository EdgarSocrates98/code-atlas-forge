# Explorer Accessibility Report — WCAG 2.2 AA (real browser audit)

Status: **executed audit** — axe-core run in a real browser against the
production build, on the real `code-atlas explorer` subprocess serving a
real index of `tests/fixtures/sample_repo`. Supersedes the static-only
review in `docs/explorer-accessibility.md` (Phase 16), which had no
browser/process execution available.

Date: 2026-10-24 · Branch: `cycle-6.1-explorer-hardening`

## Tool versions

| Tool | Version |
|---|---|
| `@axe-core/playwright` | 4.13.0 (pinned `^4.13.0` in `explorer/package.json` devDeps) |
| `axe-core` (bundled) | 4.13.0 |
| `@playwright/test` | 1.56.1 |
| Browser | Chrome for Testing 156.0.8078.4 (Playwright `chromium` project, Desktop Chrome) |
| Angular | 18.2.13 (production build `dist/explorer/browser`, served by `code-atlas explorer`) |
| Node | v22.23.2 |

Suite: `explorer/e2e/a11y.spec.ts` — run with
`npx playwright test e2e/a11y.spec.ts` inside `explorer/`. The existing
global setup indexes the fixture repo with the real CLI and boots the
real server; no request is mocked.

axe rule tags enabled: `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`,
`wcag22aa`, `best-practice`. Blocking impacts: `critical` + `serious`.
Every scan asserts **zero blocking violations**; advisory impacts
(moderate/minor/best-practice) are collected and printed — and were also
**zero** in this run.

## Routes and surfaces tested (24 scans)

Each surface was scanned in **both themes** (light and dark, driven by
the `atlas-theme` localStorage key that `ThemeService` persists; the
suite also asserts `data-theme` on `<html>` so no scan silently runs
under the wrong palette):

`/dashboard`, `/repositories`, `/graph`, `/search`, `/impact`,
`/revisions`, `/context`, `/diagnostics`, `/settings`,
`/symbols/{occurrence_id}` (real id discovered via
`GET /api/v1/search?q=hello`), plus two post-interaction states that only
mount after a real API round-trip: `/search` with live results and
`/context` with a generated pack.

## Violations found and fixed

**6 instances of `aria-prohibited-attr`** (axe impact: *serious*; WCAG
2.2 SC 4.1.2 — elements with implicit role `generic` may not be named):
`<div>` elements carrying `aria-label` without an explicit role. ARIA
prohibits naming generic elements, so the label was dead markup and a
WCAG A violation. Fixed by adding the semantically correct role; no
visual or behavioral change.

| File | Element | Fix |
|---|---|---|
| `src/app/layout/shell/shell.component.html:19` | `.shell__context` toolbar context | `role="group"` |
| `src/app/features/graph/graph.component.html:53` | `.graph-feature__filters` | `role="group"` |
| `src/app/features/dashboard/dashboard.component.ts:77` | `.dashboard__cards` metric cards | `role="group"` |
| `src/app/features/search/search.component.ts:225` | `.search__results` live results area | `role="region"` |
| `src/app/features/context/context.component.ts:137` | `.context__pack` generated pack area | `role="region"` |
| `src/app/features/impact/impact-results.component.ts:25` | `.impact-result` analysis result area | `role="region"` |

The `role="region"` choices were deliberate: these are the page's
results landmarks — a named region both fixes the prohibited attribute
and gives AT users a navigable destination for the live-updating output.

**Final axe results: 0 violations of any impact across all 24 scans**
(critical 0, serious 0, moderate 0, minor 0, best-practice advisories 0).

## Manual checks (automated where axe cannot reach)

| Check | Result |
|---|---|
| Skip link is first tab stop and moves focus to `<main id="shell-main">` | PASS |
| `:focus-visible` renders a visible outline (skip link + sidebar nav link — computed `outline-style`/`outline-width` asserted, not screenshotted) | PASS |
| Keyboard-only traversal reaches the sidebar nav and activates `/search` via Enter | PASS |
| `prefers-reduced-motion: reduce` emulated: matchMedia true, body `transition-duration` computes to `0s` (motion tokens collapse), app still navigates and `/graph` renders its stage or accessible table | PASS |

## Remaining known issues (honest list)

1. **`impact-results` region not axe-verified in rendered state.** The
   `role="region"` fix is applied by the same pattern as the verified
   search/context regions, but the fixture repo has no git revision pair
   to produce a rendered `.impact-result` — the `/impact` scans cover the
   form and state blocks only.
2. **WebGL canvas is beyond DOM audit.** The sigma.js canvas is a
   `role="application"` region with a documented keyboard map
   (`graph-kbd-help`) and runtime reduced-motion handling
   (`camera.setState` instead of `animate`), but axe cannot evaluate
   canvas internals. The accessible `<atlas-graph-table>` alternative
   (real tables, `scope="col"`, same actions) is fully audited and clean.
3. **No screen-reader pass** (NVDA/JAWS/VoiceOver) — axe + keyboard tests
   do not substitute for an AT walkthrough; same caveat as the Phase 16
   review.
4. **Diagnostics expandable snapshot rows** (the per-repository nested
   snapshots table revealed by "Listar") are behind a click and not
   covered by a dedicated scan — structure mirrors the audited outer
   table.
5. **axe cannot prove absence of keyboard traps** inside the canvas
   beyond what the focus/tab-order tests assert.

## Verification run

- `npx playwright test e2e/a11y.spec.ts` — **29/29 pass** (24 axe scans +
  summary + 4 manual checks).
- `npm test -- --watch=false --browsers=ChromeHeadless` — **160/160 pass**.
- `npm run build` — green (only pre-existing sigma/graphology CommonJS
  warnings).
- Full suite note: `e2e/graph-scale.spec.ts` is an unrelated parallel-work
  benchmark; its `@5000 nodes` case timed out at the 30 s Playwright limit
  with a trace-artifact ENOENT during teardown — observed independently of
  this change set (a11y spec itself is 29/29 green).

## Dependencies added

`@axe-core/playwright@^4.13.0` (devDependency only — test-time tool,
not shipped in the bundle; no new runtime dependencies).
