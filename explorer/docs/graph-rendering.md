# ADR: Graph rendering library for the Explorer

- Status: accepted (cycle-6, lf-0088)
- Date: 2026-10-24
- Decision drivers: 10k-node headroom, incremental expansion, camera
  stability, Angular compatibility, license, accessibility fallback.

## Context

The graph screen renders the Code Atlas relation graph: file-aggregate
nodes from `GET /graph/overview` and occurrence nodes/edges grown lazily
through `GET /graph/neighbors`. The product requirements are:

- bounded rendering (default cap 500 nodes, configurable);
- incremental expansion without re-layout of already-placed nodes;
- camera with fit/zoom/pan/reset plus min/max zoom and frozen positions
  after layout;
- labels that only appear past a zoom threshold;
- a canvas/WebGL renderer (SVG is explicitly disallowed at this scale);
- framework-agnostic enough to drive from Angular 18 signals without a
  wrapper library.

## Candidates

| Criterion | sigma.js 3.0.2 | cytoscape.js 3.33.x | vis-network 10.x |
|---|---|---|---|
| Renderer | WebGL (nodes, edges, labels in canvas layers) | Canvas 2D | Canvas 2D |
| 10k nodes | Designed for it; 10k+ nodes/edges stays interactive in WebGL | Usable but CPU-bound at 10k; needs style batching | Struggles; physics + canvas 2D degrade visibly past ~2-3k |
| Incremental expansion | Data lives in an external graphology graph; `addNode/addEdge` are surgical — no implicit re-layout ever | `cy.add()` works but every layout call repositions the whole graph unless positions are re-locked | Physics engine re-simulates on data change by default; stability requires disabling simulation after each edit |
| Layout | None built in (by design) — we run `graphology-layout-forceatlas2` once per data version, exactly matching the "layout runs once, positions freeze" requirement | Built-in layouts (cose, fcose, …) — convenient but easy to trigger accidentally | Built-in force simulation that keeps running — actively fights "frozen positions" |
| Camera | First-class `Camera` object: `setState/animate/animatedZoom/animatedReset`, `minCameraRatio`/`maxCameraRatio`, `stagePadding`, `autoRescale: false` for zero drift on data change | `cy.zoom()/pan()/fit()` — capable but the zoom model couples selection and viewport less predictably | `moveTo/fit` — basic, re-centers on stabilization |
| Label zoom threshold | Native `labelRenderedSizeThreshold` setting — one flag, exactly the requirement | Style `min-zoomed-font-size` works but is per-stylesheet | `scaling.label` per edge/node — manual |
| Edge filtering | `edgeReducer`/`nodeReducer` map data → display (`hidden`, color, size) without mutating the dataset | Stylesheet selectors + `display:none` — works | DataSet `filter` views — workable but a second data source to keep in sync |
| Angular compat | Plain TS class; lifecycle is `new Sigma(graph, el)` / `kill()` — trivially wrapped in `ngAfterViewInit`/`ngOnDestroy`, runs outside the zone | Same shape; heavier API surface to learn | Same shape; API designed around mutation observers that complicate change detection |
| License | MIT | MIT (cytoscape consortium) | MIT + Apache-2.0 dual |
| Bundle | ~150 kB min (sigma + graphology) | ~350 kB min | ~500 kB min |

## Decision

**sigma.js 3.0.2** with `graphology` 0.25.4, `graphology-types` 0.24.8,
`graphology-layout` 0.6.1 and `graphology-layout-forceatlas2` 0.10.1, all
pinned exactly.

Why:

1. WebGL renderer is the only option here that keeps the 10k-node ceiling
   honest. The default cap is 500, but the cap is a product decision, not
   a renderer limitation — headroom stays cheap.
2. The external data model (graphology `Graph`) is the cleanest match for
   incremental expansion: merging a `/graph/neighbors` response is a
   `addNode/addEdge` diff, and nothing in sigma ever moves a node on its
   own. Positions are plain node attributes → "positions freeze after
   layout" is the default behavior, not something we have to fight for.
3. `labelRenderedSizeThreshold` implements "labels only under zoom" in
   one setting instead of custom render logic.
4. `minCameraRatio`/`maxCameraRatio` + a real `Camera` object give
   deterministic fit/zoom/reset without re-normalization surprises
   (`autoRescale: false` pins the normalization once the first layout
   lands).
5. The layout being external is a feature: we invoke
   `graphology-layout-forceatlas2` explicitly once per data version and
   never on selection/expansion — the "selection never re-layouts"
   requirement holds by construction.
6. MIT license, no transitive runtime deps beyond `events` +
   `graphology-utils` (graphology adds `obliterator`).

## Rejected

- **cytoscape.js**: competent canvas renderer and the largest API, but
  10k nodes is where its 2D pipeline starts dropping frames, and the
  built-in layouts make it easy to accidentally reposition the world.
  Larger bundle, same license class.
- **vis-network**: always-on force simulation is the opposite of
  "positions frozen after layout"; disabling it per-edit is fragile, and
  2D canvas at 10k nodes is the slowest of the three.

## Consequences

- Layout is our code: one ForceAtlas2 `assign()` run per data version
  (`layoutVersion` in `GraphDataStore`), deterministic spiral placement
  for lazily expanded nodes, pinned positions restored verbatim after a
  manual re-layout.
- Sigma 4.x (released 2025) rewrites the styling/reducer API and has a
  thin migration path today; we intentionally stay on the mature 3.x API
  (`nodeReducer`/`edgeReducer`/`labelRenderedSizeThreshold`) until the
  v4 ecosystem settles. Revisit at cycle-8+.
- WebGL absence (headless CI, remote desktops) degrades to the tabular
  list view — the accessible alternative required anyway.
- Keyboard users get canvas-level pan/zoom/fit/expand/pin shortcuts plus
  a roving `[`/`]` node focus; full parity lives in the list view.
