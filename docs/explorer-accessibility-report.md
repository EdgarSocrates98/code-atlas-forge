# Explorer Accessibility Report — WCAG 2.2 AA (real browser audit)

Status: **executed audit** — axe-core run in a real browser against the
production build, on the real `code-atlas explorer` subprocess serving a
real index of `tests/fixtures/sample_repo`. Supersedes the static-only
review in `docs/explorer-accessibility.md` (Phase 16), which had no
browser/process execution available.

**Cycle-8 update (Phase 14, lf-0140, 2026-10-25, branch
`cycle-8-production-quality`):** coverage extended to the five cycle-7
domain views — `/lineage`, `/contracts`, `/infra`, `/pipelines`,
`/federation` — closing the cycle-7 NOT-RUN "new domain screens lacked
dedicated axe scans". Suite is now **48 axe scans** (24 prior + 24 new):
each domain route audited in both themes across populated *and* empty
states, plus post-interaction states (`/lineage` selected entity,
`/contracts` consumers disclosure, `/federation` no-workspace). Domain
views run against a dedicated second explorer instance
(`e2e/domain-fixture-server.ts`) that indexes the real domain fixtures
with `--domains` — isolated so the shared suite's single-repo
assertions (`flows2.spec.ts`) are untouched. **Result: 0 blocking
violations, 0 advisory, 48/48 scans.** Detail: `docs/cycle-8-a11y.md`.

**Cycle-8.1 update (Phase 7, lf-0150, 2026-10-25, branch
`cycle-8.1-ga-readiness`): post-migration re-verification on Angular
22.2.3.** Everything above ran on Angular 18.2.13; the 18→22 migration
(lf-0147) rewrote the bundle, so the whole audit was re-run verbatim on
the new production build. **Identical result: 48 axe scans, 0 blocking,
0 advisory — zero new violations, zero `src/` fixes.** The suite gained
one manual check (focus ring on the `/search` query input and `/graph`
toolbar — the two surfaces previously only covered via the shell): now
**66/66 a11y tests pass** and `e2e/screenshots.spec.ts` is **65/65**
with all 65 PNGs regenerated on the Angular 22 render. New this cycle:
Lighthouse 13.5.0 ran against a real explorer subprocess — accessibility
**100** and best-practices **100** on `/dashboard`, `/search`, `/graph`,
`/settings` (full `/dashboard` run: performance 83, seo 91 — both
informational for a loopback tool). Detail: `docs/cycle-8-a11y.md`
cycle-8.1 section.

Violations found and fixed in the cycle-8 pass:
`aria-valid-attr-value` (critical) on the `/contracts` consumers
disclosure — `aria-controls` dangled in loading/error/empty phases →
fixed with a `.contracts__consumers-region` wrapper that owns the id in
every phase; `landmark-unique` (moderate) — duplicate service names
(`app` ×3, `pets_router` ×2) → fixed by appending `qualified_name` to
service headings; a 105 px mobile-reflow overflow on `/lineage`
(unbreakable `READS_FROM/WRITES_TO/TRANSFORMS/DERIVES_FROM` token) →
fixed with `overflow-wrap: anywhere` on all five domain `__description`
blocks + identifier lists, and the overflow probe now names
content-escape offenders. One backend bug reported, not fixed
(out-of-scope `src/code_atlas/`): `GET /domains/entities/{id}/neighbors`
500s for some entities (`'<' not supported between 'int' and 'NoneType'`).

New manual checks on domain views (all PASS): skip link on a domain
route; tab order reaches `/lineage` form and in-table
`/contracts` consumers toggle (Enter expands); `/federation` workspace
button keyboard-operable; Escape is a consistent no-op (no dialogs);
`prefers-reduced-motion` zeroes all motion on populated domain views;
200 % zoom (640 CSS px proxy) reflow without page overflow on all five
routes. 29 new populated/empty/zoom screenshots under
`docs/screenshots/` via `e2e/screenshots.spec.ts` (65/65 pass).

Date: 2026-10-24 · Branch: `cycle-6.1-explorer-hardening` (original run;
cycle-8 update above)

## Tool versions

| Tool | Version |
|---|---|
| `@axe-core/playwright` | 4.13.0 (pinned `^4.13.0` in `explorer/package.json` devDeps) |
| `axe-core` (bundled) | 4.13.0 |
| `@playwright/test` | 1.56.1 |
| Browser | Chrome for Testing 156.0.8078.4 (Playwright `chromium` project, Desktop Chrome) |
| Angular | 22.2.3 in the cycle-8.1 re-run (18.2.13 in the original/cycle-8 runs; production build `dist/explorer/browser`, served by `code-atlas explorer`) |
| Lighthouse | 13.5.0 (cycle-8.1 only; `npx`, real explorer subprocess, `--chrome-flags="--headless=new --no-sandbox"`, `CHROME_PATH` = Playwright chromium-1248) |
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

## Routes and surfaces tested (48 scans — 24 original + 24 domain)

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

Cycle-8 additions (24 scans, dedicated populated fixture server —
`e2e/domain-fixture-server.ts`): `/lineage` (populated, selected entity,
empty), `/contracts` (populated, consumers expanded, empty), `/infra`
(populated, empty), `/pipelines` (populated, empty), `/federation`
(populated workspace; no-workspace on the shared server) — all ×2 themes.

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

**Final axe results: 0 violations of any impact across all 48 scans**
(critical 0, serious 0, moderate 0, minor 0, best-practice advisories 0).
The cycle-8 pass additionally found and fixed `aria-valid-attr-value`
(critical, `/contracts` consumers `aria-controls` dangling in
non-ready phases) and `landmark-unique` (moderate, duplicate service
names) — both fixed in
`features/domains/contracts/contracts.component.ts`, so the 48-scan
rerun reports zero at every impact. A 105 px mobile reflow overflow on
`/lineage` (unbreakable relation-token text) was also fixed via
`overflow-wrap: anywhere` on the domain description/list classes.

## Manual checks (automated where axe cannot reach)

| Check | Result |
|---|---|
| Skip link is first tab stop and moves focus to `<main id="shell-main">` (also verified on a domain route, `/pipelines`) | PASS |
| `:focus-visible` renders a visible outline (skip link + sidebar nav link + domain view form/table controls + cycle-8.1: `/search` query input and `/graph` toolbar button — computed `outline-style`/`outline-width` asserted, not screenshotted) | PASS |
| Keyboard-only traversal reaches the sidebar nav and activates `/search` via Enter | PASS |
| Tab order reaches `/lineage` form controls; `/contracts` in-table consumers toggle expands via Enter; `/federation` workspace button toggles via Enter | PASS |
| Escape on domain views is a consistent no-op (no dialogs/overlays exist to dismiss; same contract as native `<details>`) | PASS |
| `prefers-reduced-motion: reduce` emulated: matchMedia true, body `transition-duration` computes to `0s` (motion tokens collapse), app still navigates and `/graph` renders its stage or accessible table | PASS |
| `prefers-reduced-motion` on populated `/contracts` and `/lineage`: **every rendered element computes 0s transition/animation duration** | PASS |
| 200 % zoom (640 CSS px reflow proxy) on all five domain routes: page-level horizontal overflow ≤ 1 px, h1 visible | PASS |

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
6. **Backend `/domains/entities/{id}/neighbors` bug (reported, not
   fixed — `src/code_atlas/` out of scope):** some entities return
   `500 internal: '<' not supported between instances of 'int' and
   'NoneType'` — deterministic per entity (probe-verified on api_repo
   entity ids 102/90; 93/99/100 return 200). Product impact: `/contracts`
   consumers disclosure renders the typed error alert for affected
   endpoints — still a valid ARIA tree after the `aria-controls` fix.

## Verification run

Cycle-8.1 re-run (Angular 22.2.3, branch `cycle-8.1-ga-readiness`):

- `npx playwright test e2e/a11y.spec.ts e2e/screenshots.spec.ts` —
  **131/131 pass** in a single run: 66 a11y tests (48 axe scans + 17
  manual checks + summary; `scans: 48, blocking violations: 0,
  advisory: 0`) + 65 screenshot captures (36 core + 29 domain, all
  asserting horizontal overflow ≤ 1 px; PNGs regenerated on Angular 22).
- `npx ng test --watch=false --browsers=ChromeHeadless` — **238/238 pass**.
- `npm run build` — green (only pre-existing sigma/graphology CommonJS
  warnings).
- `npx lighthouse` (13.5.0, real explorer subprocess, headless Chrome
  for Testing 156) — accessibility **100**, best-practices **100** on
  `/dashboard`, `/search`, `/graph`, `/settings`; zero failing audits in
  either category.
- Environment note: the 131/131 run used `ATLAS_E2E_PORT=8920` and an
  isolated `--output` dir because two sibling agents held the default
  port 8743/`test-results/` concurrently; four `browserContext.close`
  trace-ENOENT teardown flakes observed under contention were all green
  on isolated re-run. Environmental, not a product defect.

Cycle-8 run (Angular 18.2.13, `cycle-8-production-quality`):

- `npx playwright test e2e/a11y.spec.ts` — **65/65 pass** (48 axe scans +
  16 manual checks + summary; `scans: 48, blocking violations: 0,
  advisory: 0`).
- `npx playwright test e2e/screenshots.spec.ts` — **65/65 pass** (36
  baseline captures + 29 new domain captures, all asserting horizontal
  overflow ≤ 1 px).
- `npx ng test --watch=false --browsers=ChromeHeadless` — **238/238 pass**.
- `npm run build` — green (only pre-existing sigma/graphology CommonJS
  warnings).
- Full suite note: `e2e/graph-scale.spec.ts` is an unrelated parallel-work
  benchmark; its `@5000 nodes` case timed out at the 30 s Playwright limit
  with a trace-artifact ENOENT during teardown — observed independently of
  this change set (a11y and screenshots specs are 65/65 green).

## Dependencies added

`@axe-core/playwright@^4.13.0` (devDependency only — test-time tool,
not shipped in the bundle; no new runtime dependencies).
