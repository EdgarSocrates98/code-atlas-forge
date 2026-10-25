# Graph density — GA (cycle-8.1, lf-0152)

Detail doc: `docs/cycle-8.1-graph-density.md`. Commit: `137555a`.

## Measured on Angular 22.2.3

| benchmark | result |
|---|---|
| Dense ceiling | 5,005 edges / 102 nodes rendered; pan **~60.2 FPS**, 0 frames >33ms; relayout wall 677ms, max main-thread gap **383.3ms**; heap 0.46→11.19→9.7MB; zero page/Sigma errors |
| Scale 10k @ cap 2,000 | API median ~49.3ms; ready ~394ms; funnel 10,000→2,000→rendered 500/2,000; pan ~57.1 FPS@500 / ~40.9 FPS@2000 |
| Heap attribution | Instrumented CDP metric reads +460.8KB/cycle — proven **DevTools remote-handle artifact** by in-page control (−0.83MB/cycle, ~12 DOM nodes retained, no positive trend) |
| Long session | 40 rounds; heap 6.34→9.50MB (+65.4KB/round slope, no leak pattern); 5 Sigma remounts ending `sigmaLive=0`; `pageErrors=[]` |

## Coverage

`graph-ceiling.spec.ts` (3 tests: dense 5k ceiling, mount/unmount heap
attribution via CDP V8 snapshot diff + in-page control, 40-round
long-session trend) + `graph-scale.spec.ts` extended to **10,000**
nodes. Full graph suite: 21/21.

## Honest framing

- The 383.3ms layout gap at 5k dense edges is the real measured
  ceiling cost — below interaction-jank catastrophe but above the
  <300ms target for smaller graphs; documented, not smoothed.
- The apparent heap "growth" in the instrumented metric is a DevTools
  artifact, not a leak — the in-page control shows no positive
  retained trend.
- `>10k` interactive nodes remains beyond validated scope (10k is the
  current measured scale entry).
