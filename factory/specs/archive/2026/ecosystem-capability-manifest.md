---
id: ecosystem-capability-manifest
title: Publish Code Atlas capability manifests for generic discovery
agent: codex
risk: medium
grill: completed
verification:
  - PowerShell `$env:PYTHONPATH='src'; python -m pytest -q tests/test_package.py`
  - uv build --wheel
  - uv run ruff check src/code_atlas tests/test_package.py
---

# Grill Gate

- Owner: ecosystem operator.
- Problem: Code Atlas exposes CLI/MCP capabilities but has no `forge.json` or
  `forge.agentic.json`, so generic Forge discovery cannot inspect it without
  knowing Python internals.
- Scope: add the existing ecosystem manifest shapes at source and package
  surfaces, with parity tests and wheel inclusion.
- Out of scope: changing indexing/search behavior, creating a central
  orchestrator, or adding runtime dependencies.
- Review failure: manifests disagree with the package version, are absent from
  the wheel, or claim a capability not represented by an existing CLI surface.
- Smallest acceptable implementation: generic operational and agentic
  descriptors that identify Code Atlas and its existing read-only/MCP flows.

# Acceptance Criteria

- Root manifests identify `code-atlas` and package version `1.1.0`.
- Package manifests are included in the wheel and match the root manifests.
- Parity tests fail on missing or divergent manifests.
- No new runtime dependency or orchestration responsibility is introduced.

# Review Notes

- The `index` workflow is explicitly mutating; all diagnostic/search/impact
  workflows are read-only.
- The package manifest is duplicated only as a distribution asset; tests keep
  it byte-semantically equivalent to the checkout manifest.

## Review evidence — 2026-11-04

- `PYTHONPATH=src python -m pytest -q tests/test_package.py` → `4 passed`.
- `uv run ruff check src/code_atlas tests/test_package.py` → passed.
- `uv build --wheel` → `code_atlas_forge-1.1.0-py3-none-any.whl`.
- Wheel inspection confirmed `code_atlas/forge/forge.json` and
  `code_atlas/forge/forge.agentic.json`.
- `code-atlas --help`, `index --help`, `search --help` and `impact --help`
  confirmed the declared command surfaces; `impact` uses the real `symbol`
  mode.
- `uv run pytest` without `PYTHONPATH` is `NOT_RUN` for this checkout because
  it does not expose the `src` layout; the reproducible project test command
  is recorded above.
- Review verdict: ACCEPTED for this spec; no runtime dependency or release
  version was changed.
