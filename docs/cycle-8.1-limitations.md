# Cycle 8.1 — limitations & NOT RUN register

Everything below was **declared, not silently skipped**. Each item
names the blocker and the revisit condition.

## NOT RUN

| item | blocker | revisit when |
|---|---|---|
| Interactive Windows/macOS Explorer session | no Windows/macOS box on this machine | a maintainer runs `code-atlas explorer` on a real Windows box (CI legs green) |
| Native Safari (non-playwright) | no Apple hardware | manual pass on macOS Safari |
| Screen readers (NVDA/VoiceOver/JAWS) | no SR environment | dedicated SR audit; axe+keyboard already green |
| Real PyPI/TestPyPI upload | requires maintainer-created Trusted Publisher + authorized tag | maintainer runs `release.yml` dispatch (Phase 16 prepared) |
| Agent-harness context-pack eval | external harness out of scope | dedicated eval cycle |
| >5k-node interactive graph session | see `docs/graph-density-ga.md` for measured ceiling | perf budget decision |

## Accepted risks (documented decisions)

- `/api/v1/diagnostics` exposes DB path on loopback — same-OS-user
  reader; re-review if API ever leaves loopback
  (`docs/security-ga-review.md`).
- `explorer_static_root()` prefers packaged over dev dist — stale-dist
  shadow hazard caught by cross-browser spec
  (`docs/explorer-wheel-packaging.md`).
- `style-src 'unsafe-inline'` kept — Angular runtime style bindings
  require it; `script-src` is strict `self`
  (`docs/angular-migration.md`).
- 2 dev-only Vite advisories remain (`npm audit` incl. dev); runtime
  audit is 0 — build tooling never ships in the wheel.
- API-contract consumers lane always empty until literal-URL↔contract
  resolution (deferred "Phase 9" in `domains/api/emit.py`) — release
  note, not a bug (`docs/cycle-8.1-journeys.md` A1).
- Python transitive deps not itemized in SBOM (no requirements.lock);
  `pip-audit` covers installed env as superset.
- Concurrent Playwright runs share port 8743 + `test-results/` —
  teardown-level trace races; isolate via `ATLAS_E2E_PORT` + `--output`.

## Product backlog (found, not fixed — by design)

- No graph-export button (export is `GET /graph/overview`; journey J8
  downloads the real payload).
- No federated-search UI surface (API + `?repo=` param only).
- PySpark `spark.sql` non-literal strings: declared unsupported, not
  silently dropped.
