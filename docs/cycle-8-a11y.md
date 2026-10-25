# Cycle 8 — Explorer Accessibility + Visual QA (Ph.14, lf-0140)

Branch `cycle-8-production-quality`. Re-audit of the WCAG 2.2 AA baseline
(cycle 6.1, 24 axe scans / 0 violations) **plus dedicated coverage of the
five cycle-7 domain views** — closing the declared cycle-7 NOT-RUN
"new domain screens lacked dedicated axe scans" (`cycle-7-limitations.md`
L6, homologation item 25/H3).

Routes under audit: `/lineage`, `/contracts`, `/infra`, `/pipelines`,
`/federation` (`explorer/src/app/app.routes.ts`, nav items in
`layout/shell/shell.component.ts`).

## Harness — dedicated populated fixture server

The shared e2e server (`global-setup.ts`) indexes only
`tests/fixtures/sample_repo` — no domain entities — so the domain views
would only render empty states, and indexing extra fixtures into the
shared data dir was rejected because other specs hard-assert the
single-repo state (`flows2.spec.ts` expects exactly one `.repo-card`).

New helper `explorer/e2e/domain-fixture-server.ts` boots a **second real
`code-atlas explorer` subprocess** (own `CODE_ATLAS_DATA_DIR` in a temp
dir, own port via `net.listen(0)`). It indexes with the real CLI:

| Fixture | Index flags | Feeds |
|---|---|---|
| `tests/fixtures/sql_warehouse` | `--domains sql` (36 entities, 32 relations) | `/lineage` |
| `tests/fixtures/terraform_infra` | `--domains terraform` (24 entities, 45 relations) | `/infra` |
| `tests/fixtures/cicd_repo` | `--domains cicd` (13 entities, 34 relations) | `/pipelines` |
| `tests/fixtures/api_repo` | `--domains api` (30 entities, 43 relations) | `/contracts` |
| `tests/fixtures/sample_repo` | default (no domain data) | empty-state scans |
| federation | `federation create e2e-ws` + 2 `add` members | `/federation` |

Tests deep-link with `?repo=<id>` (resolved by
`ExplorerContextService.syncFromQueryParams`). The populated-state root
for `/lineage` is chosen by API probe (first entity whose
`/domains/lineage` returns ≥1 edge); the `/contracts` expanded
disclosure picks an endpoint with `CALLS_ENDPOINT` in-edges when one
exists. No mocks, no fabricated state.

## axe coverage added (measured)

Same rule tags (`wcag2a/aa, wcag21a/aa, wcag22aa, best-practice`),
same blocking bar (critical+serious), both themes (`atlas-theme` +
`data-theme` attribute asserted per scan):

| Surface | Scans (light+dark) | States covered |
|---|---:|---|
| `/lineage` | 6 | populated form, entity result (lanes + edges table), empty |
| `/contracts` | 6 | populated (contract files + services + endpoints + unbound), consumers disclosure expanded, empty |
| `/infra` | 4 | populated (modules/resources/roles/relations tables), empty |
| `/pipelines` | 4 | populated (workflow cards + jobs tables + triggers), empty |
| `/federation` | 4 | populated workspace + auto-selected members table (fixture server); no-workspace empty (shared server) |
| **Total new** | **24** | |

**Suite total after this change: 48 axe scans** (24 prior + 24 new),
run result: `A11Y AUDIT SUMMARY — scans: 48, blocking violations: 0,
advisory: 0`. `npx playwright test e2e/a11y.spec.ts` → **65/65 pass**.

## Violations found and fixed

| # | Impact | File | Finding | Fix |
|---|---|---|---|---|
| V1 | **critical** `aria-valid-attr-value` (WCAG 4.1.2) | `features/domains/contracts/contracts.component.ts` | `aria-controls="consumers-{id}"` on the consumers toggle dangled whenever the expanded state was loading/error/empty — the `<ul>` owning that id only existed in the ready+non-empty phase. The 500-path surfaced by the API bug below made this reachable in practice. | New `.contracts__consumers-region` `<div>` owns `id="consumers-{id}"` in **every** expanded phase; the `<ul>` lost the id (keeps its class). |
| V2 | moderate `landmark-unique` (best-practice) | same file, service `<h2>` | api_repo has three services named `app` and two named `pets_router` → identical `role=region` accessible names (advisory ×4). | h2 now appends `(qualified_name)` muted when it differs from `name` — unique names, honest disambiguation for sighted users too. |
| V3 | reflow (SC 1.4.10 proxy — found by the overflow probe, not axe) | `features/domains/{lineage,contracts,infra,pipelines,federation}/*.component.ts` | `.lineage__description` contains `READS_FROM/WRITES_TO/TRANSFORMS/DERIVES_FROM` — a 44-char unbreakable token → **105 px page-level horizontal overflow at 375 px** (mobile). Text node overflowed while the `<p>` rect stayed in-bounds (hence the empty offender list). | `overflow-wrap: anywhere` on all five `*__description` classes + `.lineage__lane li` + `.contracts__consumers li` (same raw-identifier content class). Re-probed: overflow 0. |
| V4 | probe gap (test infra) | `e2e/screenshots.spec.ts`, `e2e/a11y.spec.ts` | `measureHorizontalOverflow` only checked element rects, so V3-type text overflow produced an unexplained `offenders: []`. | Probe now also names elements whose content escapes their own box (`scrollWidth > clientWidth+1`, `overflow-x: visible`, not contained in a clip/scroll ancestor). Same assertion, better diagnostics; kept in sync in both files. |

**Backend finding — REPORT ONLY (not fixed, `src/code_atlas/` out of
scope):** `GET /api/v1/domains/entities/{id}/neighbors?direction=in`
returns `500 internal: '<' not supported between instances of 'int' and
'NoneType'` for some entities — deterministic per entity (probe-verified:
api_repo entity ids 102 and 90 fail; 93/99/100 answer 200). Looks like a
sort over a nullable column in the neighbors handler. Product impact:
"Consumidores" disclosure on `/contracts` shows the typed
`p.contracts__error` alert for affected endpoints — honest failure, and
(after V1) still a valid ARIA tree. Owner: a `code_atlas` phase, not the
explorer.

## Manual checks (automated — what axe cannot cover)

| Check | Result |
|---|---|
| Skip link is first tab stop on a domain view (`/pipelines`) and moves focus to `#shell-main` | PASS |
| Tab order reaches `/lineage` form controls (select → input/button sequential); `:focus-visible` outline computed non-none on the select | PASS |
| Tab order reaches in-table controls on `/contracts` (`.contracts__consumers-toggle` inside the endpoints table); keyboard-focused button shows ring; Enter expands (`aria-expanded=true`) | PASS |
| `/federation` workspace button keyboard-operable (focus → Enter → `aria-pressed=true`, members table persists) | PASS |
| Escape on domain views = consistent **no-op** (no dialogs/overlays exist to dismiss — same contract as native `<details>`; disclosure stays open, no navigation, page intact). Only Escape behavior in the app remains the graph canvas selection-clear (unchanged, out of domain views). | PASS |
| `prefers-reduced-motion: reduce` on populated `/contracts` and `/lineage`: matchMedia true + **every rendered element computes 0s transition/animation duration** (motion tokens collapse; domain views define no own motion). The `state-block__spinner` reduce path is covered statically (`@media reduce { animation: none }` in `state-block.component.ts`) — its 800 ms duration is intentionally non-token but reduce-killed; left as-is. | PASS |
| 200 % zoom reflow (640 CSS px ≈ 200 % of 1280): populated `/lineage`/`/contracts`/`/infra`/`/pipelines`/`/federation` — page-level horizontal overflow ≤ 1 px, h1 visible, content reachable | PASS (5 routes) |

Note on "inspector": domain views have no inspector panel (that's the
graph canvas pattern). Their interactive surfaces — forms, tables,
in-table disclosure buttons, workspace cards — are all in the tab order
as proven above.

## Visual coverage added (`e2e/screenshots.spec.ts`)

New describe `screenshots — domain views (populated fixture server)` on
the same dedicated server — real tables/cards/disclosures, never the
loading shell:

| Captures | Count | Assert |
|---|---:|---|
| 5 routes × {desktop 1280×720, mobile 375×812} × {light, dark} | 20 | `overflowPx ≤ 1` + settled populated locator |
| `*-zoom200-light.png` (640×360) | 5 | same overflow assert |
| `*-empty-desktop-light.png` (sample_repo, no domain data) | 4 | honest pre-adapter screens |

29 new PNGs under `docs/screenshots/` (`lineage-*`, `contracts-*`,
`infra-*`, `pipelines-*`, `federation-*`). No golden diffs — same
honest-render + overflow tripwire as the existing suite.
`npx playwright test e2e/screenshots.spec.ts` → **65/65 pass**.

## Verification run (verbatim)

- `npm run build` — green (only pre-existing sigma/graphology CommonJS
  optimization warnings).
- `npx ng test --watch=false --browsers=ChromeHeadless` — **238/238 pass**.
- `npx playwright test e2e/a11y.spec.ts` — **65/65 pass**; summary line
  `scans: 48, blocking violations: 0, advisory: 0`.
- `npx playwright test e2e/screenshots.spec.ts` — **65/65 pass**; every
  capture asserts horizontal overflow ≤ 1 px.

## NOT RUN / residual caveats (honest list)

1. **Screen-reader walkthrough (NVDA/JAWS/VoiceOver) — still NOT RUN.**
   axe + keyboard/focus tests do not substitute for real AT. The cycle-7
   axe-scan gap is closed; the AT gap is not.
2. **200 % zoom is approximated** by a 640×360 CSS viewport (WCAG 1.4.10
   reflow proxy); Playwright cannot drive real browser page-zoom. Mobile
   375 px captures cover an even tighter constraint.
3. **WebGL canvas** internals remain beyond DOM audit (pre-existing;
   accessible table fallback is audited).
4. **Consumers-expanded scan covers whichever terminal state the API
   returns** — in this run an endpoint whose neighbors call 500s
   (backend bug above), so the audited expansion was the error
   disclosure, not the populated list. The populated-list shape is
   covered by the same markup contract, and `aria-controls` is now
   phase-independent.
5. **Domain-view scans cover the fixture data shape** — single-digit
   tables/lists. Very wide production data (long qualified names, many
   columns) could still reflow poorly; the overflow-wrap fixes cover the
   classes exercised.
6. `impact-results` region still not axe-verified in rendered state
   (pre-existing; fixture has no revision pair).
7. Keyboard-trap checks inside the sigma canvas unchanged
   (pre-existing).

## Files touched

- `explorer/e2e/domain-fixture-server.ts` — **new** (dedicated populated
  fixture server helper, ~200 lines).
- `explorer/e2e/a11y.spec.ts` — domain-scans describe (24 scans), 12
  manual domain checks, aggregate summary moved to file end, shared
  overflow probe upgraded, one pre-existing strict-null fix.
- `explorer/e2e/screenshots.spec.ts` — domain-screenshots describe (29
  captures), overflow probe upgraded.
- `explorer/src/app/features/domains/contracts/contracts.component.ts`
  — V1 (consumers-region id) + V2 (unique service headings).
- `explorer/src/app/features/domains/{lineage,infra,pipelines,federation}`
  `.component.ts` — V3 (`overflow-wrap` on descriptions/lists).
- `docs/screenshots/` — 29 new captures.
- `docs/explorer-accessibility-report.md` — updated with this run.
