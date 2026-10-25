# Graph hardening — Cycle 8 Phases 4+5 (lf-0131)

Failure-path and lifecycle hardening of the sigma.js canvas + the
ForceAtlas2 layout worker, verified end-to-end against the real compiled
explorer (`code_atlas explorer` serving `dist/explorer/browser`, worker
chunk served same-origin under the app CSP).

Spec: `factory/specs/inbox/lf-0131-cycle8-graph-hardening.md` ·
Branch: `cycle-8-production-quality` · E2E: `explorer/e2e/graph-hardiness.spec.ts` ·
Run of record: `2026-10-25`, numbers verbatim from
`benchmarks/results/graph-hardiness-latest.json`.

Environment of record: Linux 7.0.0-38-generic · AMD Ryzen 7 5700U
(16 cores) · 31 GB RAM · Node v22.23.2 · Chromium (Playwright).

## Bugs found and fixed

1. **Zombie worker per finished layout** (`fa2-layout-runner.ts`).
   `finish()` cleared `active` and resolved the run without calling
   `engine.cancel()` on `done`/`error` — `WorkerFa2Engine` only
   terminates its worker inside `cancel()`, so every completed or errored
   layout left one live `Worker` behind (one leaked worker per mount on
   the nav-away path — destroy raced ahead of the next `cancel()`).
   Fix: `finish()` now cancels the engine exactly once for **every**
   terminal status (`done`, `error`, `cancelled`, `stale`); `cancel()`
   routes through `finish()` so there is a single release point.
   Measured after the fix: `workersLive === 0` on every off-graph frame
   of the ×10 nav stress and after all 20 destroy cycles;
   `workersTotal ≤ runs.started` and
   `started === done + cancelled + stale + error` held in every test.
2. **Filtered-out edges kept rendering** (`graph-canvas.component.ts`
   `paintAll`). `visibleEdges` is consumed by the paint pass, not the
   structure pass (filters don't bump `dataVersion`), so an edge removed
   by a type/evidence chip stayed in the graphology graph with stale
   attributes — the canvas drew it while `hiddenEdgeCount` and the
   aria-label reported it hidden. Fix: the edge pass now writes
   `attrs.hidden = !vm` (sigma zero-fills the program record — undrawn
   AND unpickable) and clears the leftover label on hidden edges.
   Verified by the concurrent-ops e2e (`filter:calls` lands inside a
   relayout storm; final counts match the label: 60 nodes / 59 edges at
   cap 60).

## What was added (not bug fixes)

- **`ChunkedFa2Engine`** (`fa2-layout-runner.ts`): the same FA2
  `iterate()` the worker runs, sliced into ~12 ms chunks between
  `setTimeout` yields — used when no usable Web Worker exists. Progress
  emits at least once per chunk so the watchdog sees a live engine.
- **`WorkerFa2Engine` degradation contract**: Worker API absent →
  `worker-api-unavailable`; constructor throws → `worker-spawn-refused`;
  worker `onerror` before its first message (the CSP `worker-src`
  signature) → `worker-startup-error`. All three degrade to the chunked
  engine and report `onFallback` → the run resolves `done` with
  `result.fallback` set and the UI publishes
  `Layout calculado na thread principal — Web Worker indisponível (…)`
  via `reportDiagnostics`. A worker that dies **after** its first message
  is a real crash → `onError`, never a silent retry.
  Implementation detail: the matrices are now transferred as **copies**
  (`slice()`) so a failed spawn never detaches the buffers the fallback
  still needs (~100 kB at the render cap — sub-millisecond).
- **`window.__atlasGraph`** (`graph-telemetry.ts`): dev/test read
  surface — live counters `sigmaLive`/`sigmaTotal`,
  `workersLive`/`workersTotal`, `fallbacks`, per-status `runs`, the live
  `sigma` handle and a `Set` of live `Worker` handles (the e2e uses them
  to hard-kill a run and to read real viewport geometry for edge
  picking). Mutates nothing; not wired to any product UI.
- **`lastLayoutError`** on the canvas + an `Erro de layout` row in the
  diagnostics panel — a dead worker/watchdog timeout is visible without
  watching the toast, and clears on the next successful commit.

## Verification — exact commands and outputs

```
$ npm run build
Application bundle generation complete. [3.611 seconds]
Output location: /home/edgar/Documentos/projetos/forjas/code-atlas-forge/explorer/dist/explorer
(worker chunk emitted: dist/explorer/browser/worker-ZFZ4RFYV.js;
pre-existing CommonJS warnings for sigma/graphology unchanged)

$ npx ng test --watch=false --browsers=ChromeHeadless
Chrome Headless 156.0.0.0 (Linux 0.0.0): Executed 238 of 238 SUCCESS (1.144 secs / 0.97 secs)
TOTAL: 238 SUCCESS

$ npx playwright test e2e/graph-hardiness.spec.ts
  ✓ 1 rapid navigation ×10 — in-flight layout killed on every leave (6.3s)
  ✓ 2 worker killed mid-layout → watchdog degrades honestly (32.1s)
  ✓ 3 Worker unavailable → chunked fallback lays out + reports degraded (903ms)
  ✓ 4 relayout racing cap/filter/theme/expand — no mixed-state commit (1.9s)
  ✓ 5 edge click under a dense view opens the inspector (14.5s)
  ✓ 6 mount/unmount canvas ×20 — leaked sigma/workers/listeners (1.1m)
  6 passed (2.1m)
```

New unit specs (`238` total, all green): engine cleanup on `done`,
`error`, repeated runs (`fa2-layout-runner.spec.ts`); chunked engine real
FA2 run/progress/cancel/iterate-throw; Worker-unavailable and
constructor-throw fallback with telemetry counters; fallback
cancellation; store concurrent-ops suite (expand × relayout / reload /
drill / cap+filter / double relayout / pin mid-flight —
`graph-data.store.spec.ts`); diagnostics `Erro de layout` row
(`graph-diagnostics.component.spec.ts`).

## Measured numbers (verbatim from the results JSON)

Dataset: `gen_large_repo.py` dense fixture `500-c2` (500 modules × 2 ring
calls, 3 hubs × 150 fanout), index snapshot holds 5,401 relations; hub
occurrence `occ:1502` (`mod_0000.hub_0000`) used for `?focus=` deep links.

### Rapid navigation — `/graph → /search → /graph` ×10

Every cycle left mid-layout (worker live + `aria-busy` + Cancel visible
on all 10). On every `/search` frame: `sigmaLive=0`, `workersLive=0`.

| counter | value |
|---|---|
| runs | 21 started = 2 done + 19 cancelled + 0 stale + 0 error |
| workers | 21 total spawned, 0 live at end |
| sigma | 11 total mounted, 1 live at end |
| JS heap (post-GC) | 4.58 → 7.26 MB (**Δ +2.68 MB**, includes first-use noise — no warm-up on this test) |
| page errors | 0 |

### Worker killed mid-layout → watchdog

`worker.terminate()` is silent by design — the 30 s silence watchdog is
the honest detector and it fired in **29,957 ms** (SLA 45 s). The run
resolved `error`: busy cleared, Cancel hid, `lastLayoutError` populated
(`O cálculo de layout falhou — as posições anteriores foram mantidas.`),
same text in the `Erro de layout` diagnostics row, `workersLive=0`.
Recovery: a fresh `Reorganizar` completed `done` — counters
`3 started = 2 done + 1 error`.

### Worker unavailable → chunked fallback

`window.Worker = undefined` (init script) →
`fallback=worker-api-unavailable`, `fallbacks=1`, `workersTotal=0`. The
layout still completed `done` over **500 rendered nodes**; the degraded
banner was visible. Max RAF gap during the chunked pass: **133.3 ms** —
one bounded main-thread slice covers serialization + the first chunk;
iteration chunks themselves are ≤12 ms. No multi-second block.

### Concurrent ops (relayout × cap 60 × filter × theme × kbd-expand)

On `?focus=1502` (101 nodes / 100 edges). Sequence: relayout → cap→60 →
relayout → `calls` chip → theme toggle → `]` Enter `e`. Final: **60
nodes / 59 edges** (cap holds), `3 started = 2 done + 1 cancelled`,
`workersLive=0`, zero non-finite positions, zero page errors.

### Edge pick under a dense view

Focus view 101 n/100 e → 39 keyboard expansions (`]`/Enter/`e`, ≤100
edges per fetch) → **259 edges on canvas** (of 5,401 relations in the
snapshot). Click at the true viewport midpoint of `edge:101`
(513.8 px segment) → inspector `Relação` with type, `Estado de
evidência`, `Evidências` — click→inspector **71.6 ms**. Edge **hover**
affordance does not exist in this build (no `enterEdge` handler) —
click only; declared, not a regression.

### Mount/unmount ×20 (Lista ↔ Grafo toggle)

3 warm-up remounts first so the heap delta is steady-state. All 20
measured destroys happened while a layout was in flight (`workersLive=1`
right after every remount — `needsLayout` stays armed per contract, so
each remount spawns a real worker).

| counter | value |
|---|---|
| sigma | 24 total (1 initial + 3 warm-up + 20), **0 live** at end |
| workers | 24 total, **0 live** at end |
| runs | 24 started = 14 done + 10 cancelled + 0 stale + 0 error |
| JS heap (post-GC) | 6.69 → 14.70 MB (**Δ +8.01 MB** ≈ 400 KB/cycle) |
| window/document listeners (net) | **−6** (no global-scope growth) |
| element-bound listeners (net) | +1513 — **overcount by construction**: the probe counts add/remove calls; listeners on detached elements stay "live" in the tally until GC reclaims the elements. Not evidence of a leak by itself |

The heap delta is recorded verbatim, not cleared: the live-structure
counters (sigma instances, workers, global listeners) all return to
baseline, but ~400 KB/cycle of retained heap persists across remounts.
Candidates are retained detached-DOM/V8 heap slack vs. a real per-mount
retention — separating those needs a heap-snapshot diff, which this suite
does not do. Declared, not claimed resolved.

## Honest limitations / NOT RUN

- **Real CSP `worker-src` refusal: NOT RUN.** The startup-error →
  chunked-fallback path is covered by unit tests (constructor throw,
  `Worker` absent) and the engine code treats a pre-first-message
  `onerror` identically; the e2e exercises `Worker = undefined`, not a
  browser-enforced CSP refusal (the app CSP doesn't block same-origin
  workers).
- **Hover on edges: NOT RUN** — no hover handler exists to measure.
- Heap-per-cycle attribution (leak vs. slack): **NOT RUN** (needs heap
  snapshots; counters-only suite).
- 5 k-edge canvas ceiling: the dense view reached **259 rendered edges**
  via real expansions (bounded tries); a 5,000-edge render ceiling was
  **NOT RUN** — the click path measured at 259 exercises the same sigma
  picking framebuffer, but that is not a substitute for the cap-size
  number.
- `stale` results stayed 0 in every run — the generation guard is
  covered by unit tests (`isStale` flip mid-run and at completion); in
  the e2e storm the losing runs resolved `cancelled` instead, which is
  the equivalent protection at that layer.

## Files

| file | change |
|---|---|
| `src/app/features/graph/fa2-layout-runner.ts` | engine cancelled on every terminal status; `fallback` on the result; `ChunkedFa2Engine`; `WorkerFa2Engine` degradation (3 reasons) + buffer-copy transfer + worker tracking |
| `src/app/features/graph/graph-telemetry.ts` | **new** — `window.__atlasGraph` counters/handles |
| `src/app/features/graph/graph-canvas.component.ts` | telemetry wiring, `lastLayoutError`, fallback diagnostic, `hidden` on filtered edges (bug 2), sigma untrack on destroy |
| `src/app/features/graph/graph-diagnostics.component.ts` | `Erro de layout` row |
| `src/app/features/graph/fa2-layout-runner.spec.ts` | terminal-cleanup, fallback, chunked-engine, watchdog specs |
| `src/app/features/graph/graph-data.store.spec.ts` | concurrent-ops describe block |
| `src/app/features/graph/graph-diagnostics.component.spec.ts` | error-row spec |
| `e2e/graph-hardiness.spec.ts` | **new** — 6 tests above |
| `benchmarks/results/graph-hardiness-latest.json` | **new** — raw measurements |
