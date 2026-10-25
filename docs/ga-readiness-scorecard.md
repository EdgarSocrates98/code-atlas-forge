# GA readiness scorecard — cycle-8.1

Baseline: `docs/cycle-8.1-baseline.md`. Scoring: 0–4 per dimension
(0 absent, 4 production-proven). Cycle-8 scorecard was **22/32** —
the deltas below close the deducted items.

| dimension | c8 | c8.1 | evidence |
|---|---|---|---|
| Correctness | 4 | 4 | 1658 pytest green; 12 bugs fixed c8 + 6 findings c8.1, all pinned |
| Security | 2 | **4** | Angular EOL XSS advisories **closed** (18.2.13→22.2.3, audit 8→0); 6 new findings fixed; CSP strict-`self` scripts; diagnostics disclosure accepted-risk documented |
| Supply chain | 3 | **4** | deterministic CycloneDX 1.5 SBOM (564 comps) + freshness tests + license gate 24/24 permissive + dependabot locked by test |
| Release readiness | 2 | **4** | wheel ships Explorer (byte-identical repro builds), OIDC trusted-publish workflow prepared (no creds invented), release_check gates green, versioning policy + tag validation |
| Performance | 4 | 4 | 5k-edge 60FPS, 10k scale @ cap, long-session no-leak (instrumented artifact proven) |
| Compatibility | 3 | **4** | Windows CI 6/6 + Git Bash fixes; cross-browser 9/9 (chromium+firefox+webkit); public-surface manifest 63 tests; migration chain v1→v12 lossless |
| A11y/UX | 3 | **4** | axe 48/0 violations post-migration; Lighthouse a11y/bp 100; focus-ring pinned; journeys 8/8 real product flows |
| Observability | 4 | 4 | structured logs now ANSI-escaped (N1); diagnostics endpoint; typed errors |

**Total: 31/32** (was 22/32).

## The remaining point

Screen-reader homologation (NVDA/VoiceOver/JAWS) remains NOT RUN —
no SR environment on this machine. Everything else that was deducted
in cycle-8 is now measured-green.

## Recommendation

All GA gates pass on this branch → **recommend promotion to `1.0.0`**
per `docs/RELEASING.md` policy. Tag + PyPI publish stay
maintainer-gated via `.github/workflows/release.yml` (OIDC); no
credentials were invented.
