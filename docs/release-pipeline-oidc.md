# Release pipeline — OIDC trusted publishing

Spec: `lf-0158-cycle81-trusted-publish.md` (cycle-8.1, Phase 16).
Workflow: `.github/workflows/release.yml`.

## Model

Publishing uses **PyPI Trusted Publishing** over GitHub Actions OIDC:
the `publish` job mints an `id-token`, PyPI exchanges it for a
short-lived credential scoped to this repo + workflow + environment.
**No long-lived API tokens are stored or required.**

## Topology

| job | permissions | purpose |
|---|---|---|
| `build` | `contents: read` | tag↔`__version__` consistency, release gates, reproducible wheel+sdist, SBOM, `SHA256SUMS.txt`, `twine check`, artifact upload |
| `publish` | `id-token: write` (only), `contents: read` | downloads verified artifacts, `sha256sum -c`, `gh-action-pypi-publish` |

- `id-token: write` is scoped to the publish job only — build cannot
  mint tokens.
- `publish` runs **only** on manual `workflow_dispatch` with
  `target: pypi|testpypi`. Tag pushes run build+attestation but cannot
  publish; PRs can never reach the job (`if:` guard).
- The job binds to a **protected GitHub Environment** (`pypi` /
  `testpypi`) so maintainer review gates can apply.

## Maintainer setup (manual, not automated)

1. On PyPI: project → *Publishing* → add Trusted Publisher:
   owner `EdgarSocrates98`, repo `code-atlas-forge`,
   workflow `release.yml`, environment `pypi`.
2. On GitHub: Settings → Environments → create `pypi` (and optionally
   `testpypi`), add required reviewers as desired.
3. Tag release: `git tag v1.0.0 && git push origin v1.0.0` → build job
   verifies tag version equals `__version__`, runs gates, uploads
   `release-dist-<version>` with checksums + `sbom.json`.
4. Dispatch: Actions → *Release* → `target: pypi`,
   `ref: refs/tags/v1.0.0` → publish job verifies checksums and uploads
   via OIDC.

## TestPyPI validation

Dispatch with `target: testpypi` to exercise the full path against
TestPyPI (requires a `testpypi` environment + Trusted Publisher entry
on test.pypi.org). `dry-run` (default) builds and verifies without any
publish step.

## Dry-run evidence

Local dry-run of the equivalent steps (this machine, cycle-8.1):

- `SKIP_E2E=1 bash scripts/release_check.sh --skip-e2e` → gates green
  (see `docs/cycle-8.1-platform-homologation.md` §release gates).
- `bash scripts/build_release.sh` → byte-identical wheel+sdist across
  repeated builds with `SOURCE_DATE_EPOCH` (lf-0148).
- `python scripts/generate_sbom.py` → deterministic CycloneDX 1.5 BOM,
  freshness enforced by `tests/packaging/test_sbom_freshness.py`.

**NOT RUN:** real PyPI/TestPyPI upload (requires maintainer-created
Trusted Publisher entries and an authorized tag — no credentials were
invented or assumed).
