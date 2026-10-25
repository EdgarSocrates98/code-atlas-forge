# Dense-graph benchmarks — Cycle 7 Phase 0.3

Real-browser benchmark of the **dense** graph surface — symbol-level ego
networks built by `GET /graph/neighbors` — complementing
`docs/graph-performance-homologation.md`, which measured file-node
overviews that carry **zero** edges.

Run of record: `2026-10-25`, `npx playwright test e2e/graph-dense.spec.ts`
— **3/3 datasets pass** (~38 s). Numbers below are verbatim from
`benchmarks/results/graph-dense-latest.json` and the per-dataset files
`benchmarks/results/graph-dense-{500-c2,1000-c5,2000-c5}.json`.

## Headline finding — two product bugs gate the dense canvas

The benchmark is green, but its most important output is a defect report.
**No path to a WebGL canvas containing edges exists in this build.**

1. **`Sigma: edge "edge:N" can't be repaint` on any edge-bearing mount.**
   `GraphCanvasComponent.paintAll()` calls
   `graph.updateEachEdgeAttributes(updater)` **without hints**
   (`graph-canvas.component.ts:806`). Sigma 3.0.2's
   `eachEdgeAttributesUpdatedGraphUpdate` treats missing hints as "layout
   unchanged" → `refresh({skipIndexation: true})` → looks up
   `edgeProgramIndex[e]` for every edge — populated only by `process()` at
   render time. Edges added since the last render have no program index →
   throw at `sigma.esm.js:3302`. (The node handler defaults the *opposite*
   way — `!updatedFields || …` — which is why the batched node write is
   safe.) The canvas mounts only inside `@case('ready')`, i.e. after data
   is already in the store, so `?focus=`, file-drill and table→graph
   remount all hit the throw inside `ngAfterViewInit`'s try/catch →
   `markRendererFailed()` → permanent accessible-table fallback.
   Zero-edge graphs skip the repaint loop entirely — that is why
   `graph-scale.spec.ts` (overview, 0 edges) never hit it. In-session edge
   merges (expand/cap-restore) throw the same error through the Angular
   effect but would self-heal on the next RAF `process()` — except bug 2
   makes those merges unreachable anyway.
2. **`drillFile()` 400s on the seed-neighbor fetch.**
   `GET /files/{id}/occurrences` returns `id: "occ:1"` (string), but
   `ApiFileOccurrence.id` is typed `number`. `drillFile` sends
   `seeds = items.map(o => o.id)` raw to `GET /graph/neighbors` →
   `occurrence_id=occ:1` → `400 bad-param: parâmetro occurrence_id não é
   inteiro`, and `occurrenceToVM` additionally mints node ids
   `occ:occ:1` (`occNodeId(item.id)`). File drill therefore never merges a
   single edge; it lands in `phase: 'error'` (~150–200 ms). Observed UI
   texts across runs: `API respondeu 400 (bad-param): parâmetro
   occurrence_id não é inteiro / Código: bad-param` (standalone probe)
   and, in the benchmark pages, `Falha na operação` +
   `Code Atlas API está offline ou inacessível em http://127.0.0.1.` —
   recorded verbatim; the deterministic 400 is independently visible in
   the page's console/network log.

Net effect measured: `/graph?focus={hub}` **always** shows the
WebGL-unavailable banner + accessible table; double-clicking a file node
to drill always lands on the error block; `← Voltar` restores the arch
canvas correctly (165–188 ms, verified below).

## Environment

| Item | Value |
|---|---|
| CPU | AMD Ryzen 7 5700U with Radeon Graphics (16 logical cores) |
| RAM | 31 GB |
| OS / kernel | Linux 7.0.0-38-generic |
| Node | v22.23.2 |
| Browser | Headless Chromium (Playwright 1.56.1) |
| WebGL | WebGL2 via SwiftShader — `ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)` — software rendering, **slower than any real GPU** |
| UI stack | Angular 18.2.13 · sigma 3.0.2 · graphology 0.25.4 |
| Page probes | RAF-delta sampler, longtask PerformanceObserver, WebGL draw-call/vertex counters, `window.Error` proxy (captures the caught Sigma throw), CDP `JSHeapUsedSize` |

## Datasets (deterministic fixtures)

`tests/fixtures/gen_large_repo.py` gained `--calls M` (each `func_i` calls
the next M modules, ring-deterministic, imports included so calls
resolve), `--hubs H` / `--hub-fanout K` (first H modules gain `hub_h`
calling K distinct targets). Default output stays byte-identical.
Validation rejects `files<1`, `calls<1`, `calls>=files`, negative or
half-specified hub params, `hubs>files`, `hubs*fanout>files-1`,
`fanout>=files`.

Generated + indexed under `/tmp/atlas-graph-dense/` (created in earlier
runs this session — `indexMs` is `null` in the JSON because the dbs
already existed; generation/indexing were verified by replaying them).

| Dataset | Files | CALLS | IMPORTS | DEFINES | CONTAINS | Relations | Occurrences |
|---|---|---|---|---|---|---|---|
| `500-c2` | 500 | 1,950 | 1,448 | 1,503 | 500 | **5,401** | 2,503 |
| `1000-c5` | 1,000 | 6,450 | 5,445 | 3,003 | 1,000 | **15,898** | 5,003 |
| `2000-c5` | 2,000 | 12,450 | 10,445 | 6,003 | 2,000 | **30,898** | 10,003 |

All relations `resolved`. Hub occurrences used as `?focus=` targets:
`mod_0000.hub_0000` → occ `1502` / `3002` / `6002` (degree 151: 150 CALLS
+ 1 DEFINES — deliberately above the client's `NEIGHBOR_LIMIT=100`).

## Measured — API (`GET /graph/neighbors?direction=both`, curl, 5 reps)

| Dataset | limit=100 median | edges / nodes | truncated | limit=200 median | edges / nodes | truncated |
|---|---|---|---|---|---|---|
| 500-c2 | 6.45 ms (samples 5.8–7.0) | 100 / 101 | true | 6.52 ms | 151 / 152 | false |
| 1000-c5 | 7.85 ms (samples 7.2–9.0) | 100 / 101 | true | 8.38 ms | 151 / 152 | false |
| 2000-c5 | 11.76 ms (samples 10.6–12.7) | 100 / 101 | true | 12.39 ms | 151 / 152 | false |

Browser-side Resource-Timing on the same fetch inside the page:
45.7 / 52.2 / 54.8 ms (nav+Angular boot overhead included).

## Measured — Phase A: `/graph?focus={hub}` deep link

Identical outcome on all three datasets: **`surface: table`,
`rendererFailed: true`**, stage never appears.

| Dataset | wall nav→ready | nodes × edges (table) | truncation notice | max RAF gap | longtasks | Sigma error (verbatim) |
|---|---|---|---|---|---|---|
| 500-c2 | 646 ms | 101 × 100 | "…o servidor também truncou resultados" | 233 ms | 119, 188, 69 ms | `Sigma: edge "edge:3" can't be repaint` |
| 1000-c5 | 456 ms | 101 × 100 | same | 117 ms | 76 ms | `Sigma: edge "edge:6" can't be repaint` |
| 2000-c5 | 486 ms | 101 × 100 | same | 133 ms | 85, 53 ms | `Sigma: edge "edge:6" can't be repaint` |

JS heap: 1.1 MB before load → 7.8–8.1 MB after the full table phase.
RAF kept ticking throughout (liveness check ≤ 35 ms at every probe point).

### Table-surface interactions (the fallback users actually get)

| Metric | 500-c2 | 1000-c5 | 2000-c5 |
|---|---|---|---|
| Hub self-expand (no-op: same first page, no pagination) | 95 ms → 101n/100e | 81 ms → 101n/100e | 84 ms → 101n/100e |
| 15 × `Expandir` wall times | 70–88 ms | 73–79 ms | 74–80 ms |
| Counts after 15 expansions | 133n / 163e | 136n / 220e | 136n / 220e |
| Node-cap collapse 500→100 (max sync block) | 16.8 ms | 16.8 ms | 16.8 ms |
| Restore 100→500 (max sync block) | 16.8 ms | 16.8 ms | 16.8 ms |

The table stays responsive at every size — no freeze, no unbounded task
(largest single RAF gap during the whole expansion phase: 16.8 ms — one
missed frame).

## Measured — Phase B: `/graph` overview + in-session drill attempt

The overview mounts cleanly everywhere (0 edges) and is a **real**
canvas surface at dense file counts:

| Dataset | arch ready | canvas counts | pan FPS mean | RAF Δ p95 / max | draw calls / verts |
|---|---|---|---|---|---|
| 500-c2 | 436 ms | 500n / 0e | **57.8** | 16.8 / 33.4 ms | 179 / 228,081 |
| 1000-c5 | 388 ms | 500n / 0e (1,000 known) | **58.0** | 16.8 / 50.0 ms | 175 / 231,063 |
| 2000-c5 | 349 ms | 500n / 0e (2,000 known) | **58.4** | 16.8 / 33.4 ms | 173 / 231,057 |

Drill attempt per dataset (`]`×k → `Enter` → `e` on `mod_0000.py`,
precisely targeted via the inspector):

| Dataset | outcome | wall | page state (verbatim) | console evidence |
|---|---|---|---|---|
| 500-c2 | `state:error` | 195 ms | `Falha na operação — Code Atlas API está offline ou inacessível em http://127.0.0.1.` | `Failed to load resource: 400 (Bad Request)` |
| 1000-c5 | `state:error` | 182 ms | same | same |
| 2000-c5 | `state:error` | 149 ms | same | same |

Recovery via the breadcrumb `← Voltar sem perder o contexto`
(`exitDrill` → frozen arch snapshot): **canvas fully restored** in
165 / 188 / 173 ms (500 file nodes, 0 edges, cap notices correct).

## What is measurable vs not measurable

**Measurable today (numbers above):**

- `/graph/neighbors` API latency on dense egos, incl. the truncation
  boundary (client 100 vs server cap 200).
- Deep-link `?focus=` end-to-end readiness on the fallback surface.
- Accessible-table render, per-node expansion latency, hub no-op
  re-expansion, node-cap collapse/restore — all on dense ego data.
- WebGL canvas pan FPS **on the 0-edge file overview** at 500–2,000
  files (57.8–58.4 FPS SwiftShader — consistent with graph-scale's
  ~60 FPS at the 500 cap).
- RAF gaps, long tasks, JS heap, error capture — on all paths.

**Not measurable in this build (recorded as `unreachable-in-this-build`):**

- Edge render count on the canvas, FPS during pan on an edge-bearing
  graph, canvas keyboard expand/collapse latency, WebGL memory on dense
  edges — every route to `canvas + edges` is blocked: `?focus=` /
  table→graph remount throw inside `ngAfterViewInit` (fatal), and the
  only in-session edge source `drillFile` 400s before merging anything.
- The spec leaves the full canvas-metric chain wired but gated on
  `canvas_alive_with_edges`; it will produce real numbers the moment the
  bugs are fixed.

## Invariants asserted by the spec (all green)

- A graph surface (canvas **or** accessible table) renders after
  `?focus=`.
- The hub's ego fetch exposes `edges > 0` and the server-truncation
  notice is shown honestly (`truncou`).
- No main-thread freeze > 5 s anywhere (largest observed single RAF gap:
  233 ms, during initial Angular+data boot).
- RAF ticker stays alive at every checkpoint (no frozen renderer).
- Table collapse/restore returns exact expected counts.

## Phase 0.4 quick checks

```
$ cd explorer && npm audit --omit=dev
… 8 vulnerabilities (4 moderate, 4 high)
  @angular/common|compiler|core|animations <=19.2.25 — multiple 2025
  advisories (HttpTransferCache leakage/DoS, XSRF token leakage, XSS via
  i18n event-handler attributes, sanitization bypass). Fix = `npm audit
  fix --force` → @angular/* 22.2.3 (BREAKING).
```

```
$ .venv/bin/python -m pytest tests/ -q | tail -2
10 failed, 710 passed, 1 skipped in 42.60s
```

All 10 failures are in `tests/domains/api/` — **untracked work-in-progress
domain code on this branch** (`src/code_atlas/domains/api/`, same state in
`git status` before this phase): `NameError: name '_quote_step' is not
defined` inside `yaml_subset.py` cascades into `adapter-crash`
diagnostics. Unrelated to the dense-graph work —
`pytest tests/ -q --deselect tests/domains/api` → **672 passed,
1 skipped, 0 failed**.

## Files changed in this phase

- `tests/fixtures/gen_large_repo.py` — `--calls/--hubs/--hub-fanout`
  density controls (deterministic; defaults byte-identical).
- `explorer/e2e/graph-dense.spec.ts` — new real-browser dense benchmark
  (this document's data source). No other specs touched.
- `benchmarks/results/graph-dense-latest.json` + `graph-dense-*.json` —
  measured output.
- `docs/graph-dense-benchmarks.md` — this report.

No production sources were modified (read-only phase); the two bugs
above are reported, not fixed.

## Reproduce

```bash
# fixtures+indexes are cached under /tmp/atlas-graph-dense (or set
# ATLAS_DENSE_DIR); datasets via ATLAS_DENSE_DATASETS='500:2,1000:5,2000:5'
cd explorer && npx playwright test e2e/graph-dense.spec.ts
```
