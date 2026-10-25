# Graph Performance Homologation — Cycle 6.1 Phase 5

**REAL browser graph-scale benchmarks — nothing estimated.** Every number
below was measured live: a real `code-atlas explorer` subprocess serving the
compiled Angular `dist` + a real per-size SQLite `atlas.db`, driven by
Playwright headless Chromium. Raw records:
`benchmarks/results/graph-scale-latest.json` (summary) and
`benchmarks/results/graph-scale-{100,500,1000,3000,5000}.json` (per-size).

Spec: `explorer/e2e/graph-scale.spec.ts` · Fixture generator:
`tests/fixtures/gen_large_repo.py` · Run:
`cd explorer && npx playwright test e2e/graph-scale.spec.ts` — **5/5 green**.

## Environment

| | |
|---|---|
| Machine | AMD Ryzen 7 5700U with Radeon Graphics, 16 logical cores, 31 GB RAM |
| OS | Linux `7.0.0-38-generic` |
| Node | `v22.23.2` |
| Playwright | `1.56.1` |
| Browser | Headless Chrome `141.0.7390.37` (Chromium bundled by Playwright) |
| WebGL | WebGL2 via **SwiftShader** — `ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)` — **software rendering, no GPU** |

> ⚠️ All frame-rate numbers are **SwiftShader software-rendering** results in
> headless Chromium — a real physical GPU should be faster. The numbers are
> still *real measured* deltas on this hardware, not estimates, and the
> *relative* scaling (60 fps at 500 nodes → ~38 fps at 2000) is the signal.

## Method — what is actually rendered

The architecture overview (`/graph`) draws **one node per source file**; the
overview API returns **no edges**, so every run reports `edges = 0` — this
benchmark measures node-load + cap + layout + pan, **not edge rendering**.

The Explorer clamps rendering to `DEFAULT_NODE_CAP = 500` (max user cap =
`2000`); `/api/v1/graph/overview?limit=` is capped at `2000`. So at 3000/5000
files the server **truncates to 2000 items** (`truncated: true`) and the UI
renders **500** of those, hiding 1500 — **no run ever draws 3000 or 5000
nodes**. The interesting measurements at scale are the cap/hidden-node
mechanism and the raised-cap (2000) experiment below.

Fixtures are deterministic flat Python repos: `mod_NNNN.py` defines
`func_NNNN`/`KNNNN`/`mNNNN` and `import`s + calls the next module in a ring,
so `--files N` produces exactly N overview file nodes (verified: `items ==
total_files == N` for N ≤ 2000). Indexed with the real CLI
(`.venv/bin/python -m code_atlas index`, database at
`$CODE_ATLAS_DATA_DIR/atlas.db` — the CLI has no `--db` flag).

Indexing context (measured earlier in this cycle, same fixtures):
| files | symbols | occurrences | relations | index time | peak RSS |
|---|---|---|---|---|---|
| 100 | 500 | 500 | 700 | ~0.3 s | — |
| 500 | 2,500 | 2,500 | 3,500 | 0.66 s | 44 MB |
| 1,000 | 5,000 | 5,000 | 7,000 | 1.12 s | 59 MB |
| 3,000 | 15,000 | 15,000 | 21,000 | 3.13 s | 114 MB |
| 5,000 | 25,000 | 25,000 | 35,000 | 5.13 s | 164 MB |

## `/api/v1/graph/overview?limit=2000` — curl latency (5 samples, ms)

| files | samples (ms) | median | items | total_files | truncated |
|---|---|---|---|---|---|
| 100 | 3.72 · 2.67 · 3.05 · 2.59 · 2.21 | **2.67** | 100 | 100 | false |
| 500 | 6.09 · 5.84 · 5.12 · 4.81 · 5.04 | **5.12** | 500 | 500 | false |
| 1,000 | 10.83 · 8.08 · 8.69 · 9.48 · 8.03 | **8.69** | 1,000 | 1,000 | false |
| 3,000 | 20.01 · 17.34 · 16.53 · 17.43 · 17.16 | **17.34** | 2,000 | 3,000 | **true** |
| 5,000 | 25.91 · 25.46 · 24.16 · 23.62 · 23.20 | **24.16** | 2,000 | 5,000 | **true** |

API latency scales ~linearly with returned payload (2000 items ≈ 144 KB
transfer), not with files on disk — 3000 and 5000 cost about the same to
serve because both return the same 2000-item truncated page.

## Initial load — time to first rendered node

| files | nav (ms) | overview fetch (ms) | TTFN page-clock (ms) | TTFN wall (ms) | max RAF gap on load (ms) | longtasks (ms) |
|---|---|---|---|---|---|---|
| 100 | 193.8 | 43.2 | 446.7 | 453 | 83.4 | 98, 145, 61 |
| 500 | 121.4 | 45.6 | 587.3 | 590 | 50 | 53, 302, 99 |
| 1,000 | 127.6 | 8.9 | 552.9 | 557 | 33.3 | 299, 95 |
| 3,000 | 124.6 | 22.9 | 563.2 | 567 | 33.3 | 55, 301, 94 |
| 5,000 | 175.2 | 26.3 | 516.7 | 520 | 49.9 | 57, 296, 97 |

TTFN stays ~0.45–0.59 s regardless of dataset size: the visible set is
always ≤500 nodes and the initial layout block (~300 ms longtask) is paid
on that capped set, not on the 5000-file repo.

## Rendered / hidden counts (default cap 500)

| files | rendered | hidden | known to store | edges | mode |
|---|---|---|---|---|---|
| 100 | 100 | 0 | — (no warning shown) | 0 | canvas (WebGL) |
| 500 | 500 | 0 | — | 0 | canvas |
| 1,000 | **500** | **500** | 1,000 | 0 | canvas |
| 3,000 | **500** | **1,500** | 2,000 | 0 | canvas |
| 5,000 | **500** | **1,500** | 2,000 | 0 | canvas |

Counts are read verbatim from the rendered DOM (`.graph-stage` `aria-label`
`"N nós visíveis … M relações"` + the warning notice `H ocultos de K nós
conhecidos`) — the Angular `GraphDataStore` is not exposed on `window`, so
the DOM is the honest observable surface. At 3000/5000 files the store only
*knows* 2000 nodes (server `limit` truncation); the other 1000/3000 file
nodes never reach the client.

## Pan FPS — 3 s circular mouse drag (RAF deltas)

**At the default cap — always 500 rendered nodes:**

| files | frames | fps mean | delta mean (ms) | p95 (ms) | max (ms) | frames >33 ms | draw calls | draw verts |
|---|---|---|---|---|---|---|---|---|
| 100 | 205 | **60.2** | 16.62 | 16.8 | 16.8 | 0 | 133 | 37,227 |
| 500 | 205 | **60.2** | 16.60 | 16.7 | 16.8 | 0 | 141 | 180,063 |
| 1,000 | 205 | **60.3** | 16.59 | 16.7 | 16.8 | 0 | 137 | 180,051 |
| 3,000 | 204 | **59.7** | 16.75 | 16.8 | 33.3 | 0 | 138 | 180,054 |
| 5,000 | 204 | **59.8** | 16.72 | 16.7 | 33.4 | 0 | 135 | 180,045 |

The cap works: once the set is capped at 500, pan is a flat ~60 fps
**independent of repo size** — a 5000-file repo pans identically to a
500-file one, because only 500 nodes ever reach the canvas. Vertex counts
confirm real per-node geometry (≈36k verts at 100 nodes → ≈180k at 500).

## Cap mechanism at the real limit — raise cap 500 → 2000

For datasets that overflow the cap, the cap input was raised to 2000 **before
any relayout** (see the product bug below — after the first relayout the
canvas no longer tracks the store, so ordering matters for honest numbers).

| files | apply block (max RAF gap, ms) | rendered after | hidden after |
|---|---|---|---|
| 1,000 | 83.3 | 1,000 | 0 |
| 3,000 | 150.1 | 2,000 | 0 |
| 5,000 | 150.0 | 2,000 | 0 |

`setNodeCap` reuses existing positions and **does not request a layout** —
the 1500 restored nodes render near the origin until a relayout; the apply
block (83–150 ms) is the synchronous graphology update, not a layout.

**Pan at raised cap (rendered = 1000 or 2000 nodes):**

| files | rendered | fps mean | delta mean (ms) | p95 (ms) | max (ms) | frames >33 ms | draw calls | draw verts |
|---|---|---|---|---|---|---|---|---|
| 1,000 | 1,000 | **49.5** | 20.18 | 33.4 | 66.7 | 4 | 163 | 378,111 |
| 3,000 | 2,000 | **40.8** | 24.50 | 66.7 | 133.3 | 26 | 152 | 744,084 |
| 5,000 | 2,000 | **40.2** | 24.88 | 66.6 | 133.3 | 26 | 157 | 744,099 |

Raising the cap to 2000 is the real cliff: ~60 fps → **~40 fps** with p95
frames at ~67 ms and ~19% of frames over 33 ms in software rendering. At
the same 2000 rendered nodes, the 3000- and 5000-file repos pan
identically (40.8 vs 40.2) — the bound is visible-node count, not repo
size.

## ForceAtlas2 relayout ("Reorganizar" button) — synchronous main-thread block

Measured as the largest RAF gap produced by the click (the layout runs
synchronously in a later task than the DOM event):

| rendered nodes | max gap (ms) | settle (ms) |
|---|---|---|
| 100 | 50.0 | 1,604.3 |
| 500 | 233.3 | 1,608.3 |
| 1,000 | 600.0 | 1,607.3 |
| 2,000 | **2,200.0 – 2,233.2** | 2,305.3 – 2,338.5 |

Superlinear, as expected for O(n·iters) ForceAtlas2 (iteration count is
clamped to 80–400 by `layoutIterations`): **~2.2 s of frozen main thread at
2000 nodes** — the page is completely unresponsive during the relayout.

## ⚠️ PRODUCT BUG discovered (recorded, NOT fixed — Phase 5 is read-only on feature code)

**The canvas dies permanently after the first user-triggered relayout.**
`GraphCanvasComponent` syncs Sigma inside an Angular signal `effect()`;
`runLayout()` ends in `store.reportPositions()` → `commitData()`, which
writes the `nodes` signal *from inside the effect that reads it*. In Angular
18.2 production mode this self-invalidating write appears to kill the
effect's future scheduling. Store-bound DOM (aria-label, counters) keeps
updating; Sigma never repaints again.

Measured identically at **every** size — the second "Reorganizar" click
(`second_relayout_probe`):

| files | 2nd-relayout max RAF gap (ms) | draw calls in window | frozen? |
|---|---|---|---|
| 100 | 16.8 | 0 | **true** |
| 500 | 16.8 | 0 | **true** |
| 1,000 | 16.8 | 0 | **true** |
| 3,000 | 16.8 | 0 | **true** |
| 5,000 | 16.8 | 0 | **true** |

A live probe in this session showed the same on cap changes: before the
first relayout a cap change produced draw calls; after it, **0 draws** while
the aria-label still advanced — the UI can display "2000 nós visíveis" over
a canvas still showing the old 500-node layout. Suggested direction for the
fix cycle: defer the position commit out of the effect (e.g. `afterRender` /
`untracked` write, or emit positions through a non-signal channel), then
re-run this spec to confirm `draw_calls_in_window > 0`.

## JS heap (CDP `Performance.getMetrics`, MB)

| files | before load | after render | after pan | after cap raise+relayout |
|---|---|---|---|---|
| 100 | 1.1 | 6.3 | 6.4 | — |
| 500 | 1.1 | 6.4 | 7.2 | — |
| 1,000 | 1.1 | 6.4 | 7.2 | 6.7 |
| 3,000 | 1.1 | 6.0 | 6.4 | 10.3 |
| 5,000 | 1.1 | 6.4 | 7.3 | 8.1 |

Heap stays ≈6–10 MB throughout — memory is a non-issue at these sizes; the
cost is CPU (layout) and raster (SwiftShader) work.

## Honest limitations

- **No edge rendering measured.** The overview API emits zero edges; edge
  cost is unexercised here. Symbol-level graphs are fetch-bounded (≤2500
  nodes) and weren't measured this phase.
- **3000/5000 nodes were never all on screen** — the API truncates at 2000
  and the canvas cap at 500/2000. The scale evidence is the *mechanism*
  (truncation flag, hidden counts, cap raise), not a 5000-node draw.
- **SwiftShader ≠ real GPU.** FPS numbers are software-rendering lower
  bounds; the 2000-node pan result (~38–41 fps) should be treated as
  "at most this bad on real hardware, possibly better".
- **Angular store internals are not observable** from the page (`window` has
  no handle); counts come from the DOM surface only. The frozen-canvas bug
  is inferred from draw-call + RAF-gap + DOM-count evidence, not from
  Angular devtools internals.
- **`draw_verts` is approximate**: `count × instanceCount` summed over
  `drawElements*/drawArrays*` — it tracks geometry scale (36k→180k→744k
  across 100→500→2000 nodes) but ignores shaders and multi-program calls.
- Single run per size (no repetition/variance band); re-runs of this spec
  jittered by a few ms/fps (e.g. cap=2000 pan 37–41 fps across runs) — the
  JSON files and this table reflect one completed green run, so treat
  single-digit deltas as noise, not signal.
- The Playwright suite needed tracing disabled (`test.use({trace:'off'})`)
  to avoid a trace-artifact ENOENT race on `page.close()` — unrelated to
  the app under test; noted for reproducibility.

## Files changed in this phase

- `tests/fixtures/gen_large_repo.py` — deterministic synthetic repo generator.
- `explorer/e2e/graph-scale.spec.ts` — the benchmark spec (this document's
  data source).
- `benchmarks/results/graph-scale-{100,500,1000,3000,5000}.json` and
  `graph-scale-latest.json` — raw measured records.
- `docs/graph-performance-homologation.md` — this report.

No `src/code_atlas/` or Explorer feature source was modified; the
frozen-canvas bug is reported, not fixed.
