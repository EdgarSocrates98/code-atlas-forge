# Explorer packaged in the wheel + reproducible builds (cycle-8.1, lf-0148)

Spec: `lf-0148-cycle81-wheel-packaging.md`. Commit: `60f8cb8`.

## What

`pip install code-atlas-forge` now ships the complete compiled
Explorer — `code-atlas explorer` works from a clean wheel install
with no Node.js, no `npm ci`, no dev checkout.

## Mechanism

- `pyproject.toml`: `code_atlas = ["explorer_dist/**"]` package-data.
- `src/code_atlas/api/server.py::explorer_static_root()`: prefers
  packaged `code_atlas/explorer_dist` resources, falls back to dev
  `explorer/dist` for repo checkouts. **`index.html` presence is
  required** — a directory without it is never served.
- `cli.py` `explorer`/`api` commands resolve through
  `explorer_static_root()`.
- `scripts/build_release.sh` builds the Explorer dist, copies it into
  `src/code_atlas/explorer_dist/`, then builds wheel + sdist with
  `SOURCE_DATE_EPOCH` for deterministic archives.

## Measured evidence

- Wheel contains **34 Explorer assets**; sdist contains the packaged
  assets.
- Repeated builds produce **byte-identical** wheel and normalized
  sdist (`SOURCE_DATE_EPOCH` pinned).
- Clean-venv install test served `/`, `/graph`, `/domains`, hashed
  assets, `/api/v1/health` — all from the wheel, no dev tree.

## Known hazard (flagged by lf-0149, accepted)

`explorer_static_root()` prefers the packaged copy even inside a dev
checkout — a stale `explorer_dist/` can shadow a fresher
`explorer/dist`. The release build always regenerates it, and the
cross-browser e2e spec catches a stale dist (Beasties `onload=`
regression was found exactly this way). Documented; revisit if dev
confusion recurs.
