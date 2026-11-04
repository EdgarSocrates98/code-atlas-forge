You are implementing Loop Factory spec `ecosystem-capability-manifest`.

        Native adapter: `codex`
        Spec path: `E:\projetos\FORJAS\code-atlas-forge\factory\specs\active\ecosystem-capability-manifest.md`
        Spec hash: `1954cb9b6e9c06a58f3d3c9cf70d513de2b8ee79e75e3c93e8c157aea4b0cc42`

        Operating rules:
        - Treat spec as source of truth.
        - Automate code generation and verification, not product decisions.
        - If spec is ambiguous, make smallest reversible assumption and record it in implementation notes.
        - Keep changes scoped to acceptance criteria.
        - Update living spec only for facts learned from implementation or tests.
        - Do not archive spec. Review step does that.
        - Use Codex subagents when work splits cleanly. Ask explicitly before spawning. Keep final answer terse with files changed and checks run.

        Required verification:
        - `uv run pytest -q tests/test_package.py`
- `uv build --wheel`
- `uv run ruff check src/code_atlas tests/test_package.py`

        Deliverables:
        1. Implement acceptance criteria.
        2. Run verification commands or explain why unavailable.
        3. Add notes under `factory/runs/` or in final response: changed files, checks, open risks.
        4. Leave spec in `factory/specs/active/` for review.

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
