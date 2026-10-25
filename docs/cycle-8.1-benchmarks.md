# Cycle 8.1 — benchmarks index

Raw artifacts: `benchmarks/results/`. Full tables in the per-spec docs.

## Real-world indexing (cycle-8 corpus, re-verified this cycle)

| repo | files | symbols | evidence | index time |
|---|---|---|---|---|
| requests | 121 | 945 | 3953 | 1.484s |
| jaffle_shop / terraform-aws-s3 / click / axios | see `docs/cycle-8.1-platform-homologation.md` §1 | | | |

## API latency (n=20, requests index)

health p50 1.69ms · search p50 37.21ms · graph/overview p50 5.15ms

## Explorer graph (Angular 22.2.3)

| scenario | result |
|---|---|
| dense ceiling 5,005 edges / 102 nodes | pan ~60.2 FPS, 0 frames >33ms; relayout 677ms wall, max gap 383.3ms |
| scale 10k @ cap 2,000 | API median ~49.3ms, ready ~394ms, pan ~40.9 FPS@2000 |
| long session ×40 | heap 6.34→9.50MB, +65.4KB/round, sigmaLive=0, pageErrors=[] |

Artifacts: `graph-ceiling-*.json`, `graph-scale-*.json`,
`graph-dense-*.json`, `graph-hardiness-latest.json`.

## Suite size trend (this branch)

1511 → 1589 → 1658 tests as specs landed; final run 102.21s, 1 skip.
