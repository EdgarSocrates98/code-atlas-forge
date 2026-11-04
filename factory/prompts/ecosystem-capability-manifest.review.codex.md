Review Loop Factory spec `ecosystem-capability-manifest` against current working tree.

        Native adapter: `codex`
        Spec path: `E:\projetos\FORJAS\code-atlas-forge\factory\specs\active\ecosystem-capability-manifest.md`

        Review stance:
        - Findings first. Focus correctness, regressions, tests, security, maintainability.
        - Compare implementation against acceptance criteria.
        - Run or inspect verification evidence:
        - `PowerShell `$env:PYTHONPATH='src'; python -m pytest -q tests/test_package.py``
- `uv build --wheel`
- `uv run ruff check src/code_atlas tests/test_package.py`
        - If accepted, say `ACCEPTED`.
        - If not accepted, say `CHANGES_REQUESTED` and list blocking items.
        - Do not move files. Operator or CLI archive step moves accepted specs.

        Spec:
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
