# Cycle 6, Phase 18 — real Playwright E2E

## Honesty note on execution (read this first)

This session's tool access was `read` / `edit` / `write` / `grep` /
`find_file_by_name` only — **no shell/bash execution tool was available**,
despite the task brief saying "YOU HAVE SHELL." I could not run `npm install`,
`npx playwright install chromium`, or `npx playwright test`, and I am not
going to fabricate command output. Everything below is written to be
runnable as-is and was checked by close reading of the Angular routes,
component templates, and `src/code_atlas/api/server.py`/`cli.py`, but **it
has not been executed**. Whoever picks this up next needs to run the
commands in "How to run" and report the real output. Any gap between what's
claimed here and the actual first run should be treated as a bug in this
harness, not in the app.

## What was built

- `explorer/playwright.config.ts` — single Chromium project, serial
  execution (tests share one running server), traces/screenshots on
  failure only, HTML report under `explorer/e2e/report/` (gitignored).
- `explorer/e2e/global-setup.ts` — indexes `tests/fixtures/sample_repo`
  with the real CLI (`python -m code_atlas.cli index`) into an isolated
  temp `CODE_ATLAS_DATA_DIR`, then spawns `python -m code_atlas.cli
  explorer --port 8743` as a real subprocess and polls
  `/api/v1/repositories` until it answers.
- `explorer/e2e/global-teardown.ts` — `SIGTERM`s the explorer subprocess by
  pid (stashed via env var, not module state, since Playwright does not
  guarantee setup/teardown share a module instance). `ThreadingHTTPServer`
  has `daemon_threads = True`, so killing the parent process is enough —
  no orphaned worker threads survive it.
- `explorer/e2e/flows.spec.ts` — 10 flow tests (see table below).
- `explorer/e2e/security.spec.ts` — 8 hardening tests, documented in
  `docs/explorer-security.md`.
- `explorer/package.json` — added `@playwright/test` (pinned `1.48.2`) as a
  devDependency and `npm run e2e` / `npm run e2e:report` scripts.

## Prerequisites this harness assumes

1. `cd explorer && npm install` has been run (installs `@playwright/test`
   from the pinned version above).
2. `npx playwright install chromium` has been run — or, if the Playwright
   browser download is blocked in this environment, set
   `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1` and use `channel: 'chrome'` in
   `playwright.config.ts` against a system Chrome install (not configured
   by default here since I can't verify which is available; documented as
   a fallback, not wired in, to avoid silently picking a config that was
   never run).
3. `cd explorer && npm run build` — the harness serves the **compiled**
   `dist/explorer/browser` output via `code-atlas explorer`, matching
   production, not `ng serve`.
4. A Python environment with `code-atlas-forge` installed
   (`pip install -e .[dev]` from repo root) so `python -m code_atlas.cli`
   resolves. If a different interpreter is needed, set `ATLAS_PYTHON`.

## How to run

```bash
cd explorer
npm install
npx playwright install chromium
npm run build
npm run e2e
```

Everything (indexing, server boot/teardown, test run) is driven by
`npm run e2e` — no separate server process needs to be started by hand.

## Flow coverage (`flows.spec.ts`)

| # | Flow | What it proves |
|---|---|---|
| 01 | Explorer loads, shell renders | SPA boots, static assets served correctly by the real server |
| 02 | Repositories route | Real API round-trip reaches a terminal state, not stuck loading/offline |
| 03 | Search executes | Form submit hits `/api/v1/search` for real, renders a result or an explicit non-offline state |
| 04 | Graph page loads | `/graph` route renders its heading (table view — no canvas/WebGL assertions, per task scope) |
| 05 | Impact form renders | Form + repository `<select>` populated from the real API |
| 06 | Context pack generates | Full form-submit → API → UI round trip for `/api/v1/context` |
| 07 | Theme toggle | `aria-pressed` flips and persists across reload (localStorage) |
| 08 | Keyboard navigation | Skip link + Tab/Enter reach and activate the main content and a nav link |
| 09 | API-disconnected recovery | Blocking `**/api/v1/**` at the browser network layer surfaces the explicit "API indisponível" state, not a silent hang |
| 10 | SPA fallback | Unknown route still resolves to a known screen (client-side wildcard route) |

None of these were skipped at write time, but two are flagged as
**honesty risks** worth checking on first real run:

- **03 (search)**: the fixture repo (`tests/fixtures/sample_repo`) is tiny
  (`src/hello.py`, `src/util.py`). If `IndexCoordinator` doesn't produce a
  lexical hit for `"hello"` in that fixture, the test still passes (it only
  asserts the UI reaches a non-offline terminal state, not that results are
  non-empty) — intentionally loose, since faking a guaranteed-match query
  would be making a product-data assumption I can't verify from static
  reading. If the first run shows this is too loose, tighten it to assert
  `res.items().length > 0` once someone confirms the fixture produces hits.
- **05 (impact)**: only asserts the placeholder option is present, not that
  a real repo appears in the `<select>`, for the same reason — I could not
  run the indexer to confirm `root_handle` values from the fixture.

## Security coverage (`security.spec.ts`)

See `docs/explorer-security.md` for the full guard-by-guard table. Summary:
8 tests, all against the real server, no mocks — bad `Host`, bad `Origin`,
no-`Origin`-allowed, oversized body (413), path traversal, no cookies,
security headers present, and OPTIONS preflight rejection.

## What I did not do, and why

- Did not touch `src/code_atlas` or `features/*` production code, per the
  task boundary. `docs/explorer-security.md` records the one real gap found
  (missing CSP header) as a finding, not a fix.
- Did not invent a visual-regression or Lighthouse pass — not asked for,
  and would need a baseline screenshot nobody has reviewed yet.
- Did not wire a `webServer` block in `playwright.config.ts` — Playwright's
  built-in `webServer` can only start *one* process and wait on *one* port;
  this harness needs an indexing step *before* the server boots, so that
  logic lives in `global-setup.ts` instead. This is a deliberate deviation
  from the typical Playwright template, documented here so it isn't mistaken
  for an oversight.
