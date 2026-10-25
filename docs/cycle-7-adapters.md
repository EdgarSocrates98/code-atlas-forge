# Cycle 7 — Domain adapters: optional posture, security and packaging (lf-0125)

Status: implemented on `cycle-7-domain-intelligence-federation`. This
doc consolidates the Phase 17/18/19 gates: measured extraction cost,
secret/no-network/no-execution assertions, and the packaging posture
of the six built-in adapters (`sql`, `pyspark`, `aws-data`, `api`,
`terraform`, `cicd`).

## Optional adapters — posture recap (ADR-0010)

Adapters are **opt-in by registration, never mandatory**:

- nothing activates without an explicit `DomainRegistry.register()` —
  `IndexCoordinator(domain_registry=None)` produces exactly zero domain
  work and preserves the pre-Cycle-7 index byte-for-byte in behavior;
- `discover_entrypoints()` exists but is opt-in by call — third-party
  code is never imported implicitly (ADR-0014);
- absence is honest: `get()` → `None`, `enabled()` omits, `require()`
  raises `UnknownDomainError`; disabled domains emit zero facts;
- enabling/disabling/upgrading an adapter changes `analyzer_versions`,
  so it defeats the digest no-op by design — the index never silently
  mixes extraction configurations.

## Packaging: zero new mandatory dependencies

All six adapters are **stdlib + `code_atlas` only** — HCL, YAML-subset,
SQL tokenizing, OpenAPI parsing and PySpark AST walking are implemented
in-repo (no `pyyaml`, `hcl2`, `sqlglot`, `openapi-spec-validator`,
`boto`, …). Verified:

- `test_domain_packages_import_only_stdlib` — every direct import under
  `domains/`, `federation/`, `eval/` resolves to `sys.stdlib_module_names`
  or `code_atlas.*` (relative imports counted internal). Transitive
  deps through `code_atlas.parsers` (tree-sitter, `ApiContractAdapter`'s
  `ParseEngine`) pre-date Cycle 7 and are the only runtime deps —
  pinned by `test_runtime_dependencies_are_pinned_tree_sitter`;
- `python -m build` wheel still builds unchanged — adapters ship inside
  the same `code_atlas` wheel; there is no `extras` for domains because
  there is nothing extra to install.

## Security/privacy assertions (tests/security/test_domain_security.py)

| Check | Assertion |
|---|---|
| Secret values | Terraform fixture secrets (`.tf` `default`s of sensitive vars + `.tfvars` assignments, grepped dynamically via `is_sensitive_name`) never appear in extracted entities/relations/evidence/diagnostics — **nor in persisted `domain_entities`/`domain_relations`/`evidence` rows** after a real `index()` |
| No network | Zero `http`/`socket`/`urllib`/`boto*`/`requests`/`asyncio`/`ssl`… imports under `domains/` + `federation/` + `eval/` (AST scan) |
| Loopback-only | No `0.0.0.0`/`INADDR_*` in domain code; `api` boundary still defaults `host=127.0.0.1` and refuses public binds with `ApiError` (unchanged) |
| No execution | Zero `subprocess`/`pty`/`multiprocessing` imports and zero `eval`/`exec`/`compile`/`import_module`/`os.system`/`os.popen` calls — adapters use `ast`/tokenize/text only |
| Expression markers | `${{ secrets.* }}` → `<secret>`, `${{ … }}` → `<expr>`, `${…}` → `${dynamic}` — expressions never materialize as concrete values; raw `${{`/`secrets.X` text never stored |
| Containment | `GitHubActionsAdapter.for_workspace` reader refuses `..` and absolute paths (`missing-content`, zero reads outside root) |

Windows compatibility fix landed with this gate: the cicd workspace
reader and the spark symlink check now use `Path.is_relative_to`
instead of `str(...) + "/"` prefix matching — the old form broke
containment on Windows separators and accepted sibling prefixes
(`/repo-evil` under `/repo`). `test_no_os_path_or_separator_concat_*`
keeps domain code pathlib-only.

## Measured cost (Phase 17 — `benchmarks/bench_domains.py`)

Reference numbers on the dev machine (Linux 6.x, CPython 3.12.3 —
measured, not targets; `benchmarks/results/domains-latest.json`):

| Adapter | Fixture | Artifacts | Median | p95 | Entities | Relations |
|---|---|---|---|---|---|---|
| sql | `sql_warehouse` | 4 | 4.5 ms | 4.6 ms | 36 | 32 |
| pyspark | `pyspark_repo` | 7 | 8.3 ms | 8.5 ms | 37 | 62 |
| aws-data | `aws_repo` | 13 | 8.6 ms | 9.1 ms | 63 | 60 |
| api | `api_repo` | 12 | 5.0 ms | 5.2 ms | 30 | 45 |
| terraform | `terraform_infra` | 13 | 3.8 ms | 3.9 ms | 24 | 45 |
| cicd | `cicd_repo` | 4 | 2.8 ms | 2.8 ms | 13 | 34 |

Synthetic scale (50 generated `.sql` files, `SqlDomainAdapter`):
median **65.9 ms**, ~**1.3 ms/artifact**, 844 entities / 650 relations,
zero diagnostics — per-artifact cost is flat vs. the fixture regime.

Index delta (`crossdomain_ws`, 5 files, fresh DB per run, median of 7):
`domain_registry` off **44.3 ms** vs. all-adapters on **55.1 ms** —
**+10.8 ms** total; the coordinator's own `stage_ms_domains` stage
accounts for ~7 ms, remainder is domain-row persistence inside the same
publish transaction. Snapshot isolation unchanged: domain rows persist
in the same `BEGIN IMMEDIATE` as the code graph.
