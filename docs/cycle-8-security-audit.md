# Cycle 8 — Security Audit (Ph.11, lf-0137)

Branch `cycle-8-production-quality`. Re-audit of the Cycle 7 baseline plus
adversarial verification of every declared boundary. Local-first, read-only
indexer: repository content is **untrusted** — never executed, imported,
evaluated, or allowed to leak across paths/repositories/snapshots.

## Scope audited

| Package | `.py` files | Focus |
|---|---:|---|
| `code_atlas.domains` | 51 | adapters, redaction, extraction |
| `code_atlas.federation` | 4 | workspace/member/snapshot scoping |
| `code_atlas.search` | 17 | FTS5 `MATCH`, `LIKE`, tokenizer |
| `code_atlas.eval` | 5 | evaluation harness |
| `code_atlas.indexing` | 7 | coordinator, cache, verified reads |
| `code_atlas.api` | 3 | HTTP surface, static, rate limit |
| `code_atlas.discovery` | (scanner/models) | inventory, secret patterns, symlinks |

## Unsafe-execution sweep (static, AST-level)

| Vector | Result |
|---|---|
| `import subprocess` / `from subprocess` | **2 sites, both authorized**: `gitbin.py` (git-only gateway), `resolvers/lsp/spawn.py` (opt-in LSP, explicit user config). None in domains/federation/search/eval/indexing. |
| `pty` imports | 0 |
| `eval(`/`exec(` builtin calls | 0 |
| `compile(` builtin (source strings) | 0 — all 65 `compile` attr hits are `re.compile` |
| `__import__`/`import_module`/`os.system`/`os.popen` calls | 0 |
| `ast.literal_eval` | present (aws adapter) — restricted literal parsing, safe by contract |
| `spark/walk.py` `*_eval` methods | internal AST interpreter for PySpark dataflow — not Python `eval`; verified |

Regression guard: `tests/security/test_cycle8_adversarial.py::TestNoUnsafeExecutionSweep`
re-runs this sweep over `domains/`, `federation/`, `search/`, `eval/`,
`evaluation/`, `indexing/` on every run.

## Findings — real, fixed, tested

| # | Severity | File:line (before) | Finding | Fix | Test |
|---|---|---|---|---|---|
| F1 | **High** | `api/server.py` `_serve_static` | Static containment used string `startswith` — sibling-prefix escape (`static-evil/` under `static/`) served arbitrary outside files; `GET /../static-evil/x.json` returned `{"pwn": true}` (probe-verified). | `Path.is_relative_to(root)` after `resolve()`. | `TestStaticTraversal` (3 vectors incl. `%2e%2e`) |
| F2 | **High** | `discovery/scanner.py` `scan()` | Symlink with benign name → secret-named target (`prod.tfvars -> .env`, `.github/workflows/ci.yml -> ../../.env`) was hashed+inventoried; `.env` content reached adapters (`STAGE=hunter2` landed in `tfvars_env_hints` — probe-verified). | In-root symlink whose resolved basename matches `SECRET_PATTERNS` counts as secret (never hashed/inventoried). | `TestSymlinkSecretAlias` (3 cases) |
| F3 | Medium | `discovery/models.py` `SECRET_PATTERNS` | `.env`-shaped names `prod.env`, `config.env`, `.envrc` not classified secret → inventoried+hashed. | Added `*.env`, `.envrc`, `.envrc.*`. | `TestEnvNameCoverage` (10 names + e2e scan) |
| F4 | Medium | `domains/cicd/workflow.py` `_csv_keys`/`_triggers`/`_matrix`/`_jobs`/`_map_summary`/`_trigger_details` | `${{ secrets.* }}` in YAML **keys** (trigger kinds, job ids, env keys, matrix axes) flowed raw into entities/relations/diagnostics — `sanitize()` covered values only (probe-verified `${{ secrets.PWNED }}` in output). | `sanitize(k.value)`/`sanitize(it.value)`/`sanitize(on_node.value)` at every identifier site. | `TestCicdExpressionNeverConcrete` (4 cases) |
| F5 | Medium | `domains/terraform/refs.py` + `tfvars.py` + `extract.py` | Credential-shaped **values** under benign keys (`description = "AKIA…"`, `stage = "AKIA…"`, `variable "environment" default = "AKIA…"`) bypassed name-based redaction and reached `tfvars_env_hints`/`env_default`. | `SECRET_VALUE_RE` (AKIA/ASIA, PEM header, `ghp_*`/`github_pat_`, `xox*`, `AIza`, `sk_live_`, JWT) → `mask_secret_values` inside `sanitize_value`; `_assignment` marks such rows `redacted`; `_env_var_default` refuses them. | `TestSecretShapedValues` (6 cases incl. `.tfvars.json`, env-hint channel) |
| F6 | Medium | `api/server.py` `_body`, `api/handlers.py` `server_body` | `Content-Length: -1` → `read(-1)` blocked until request timeout (probe-verified ~30s); non-integer → `ValueError` → 500 `internal`. | `_content_length()`: int-parse + `0..MAX_BODY` bound → typed 400/413 before `read()`. | `TestContentLengthGuard` + `TestHostileContentLengthEndToEnd` (raw-socket) |
| F7 | Medium | `domains/{terraform,sql,aws,api,spark}` `_read`/`_read_artifacts` | `base/art.path` + `read_bytes()` — absolute/`..`/swapped-symlink paths read outside root (TOCTOU between scan and extract; also direct API construction of `DiscoveredArtifact`). | `artifact_path_confined()` (scanner, shared) rejects absolute/`..`/escaping-resolve; `read_file_verified()` opens with `O_NOFOLLOW`+regular-file check. New diag `artifact-outside-root`. | `TestAdapterPathContainment` (5 adapter vectors + unit + symlink-swap) |
| F8 | Low (pre-existing, **reported — not fixed**) | `eval/impact_truth.py:38` | Untracked working-tree file (earlier Cycle-8 impact work) uses `import os` for `os.environ` — trips the Cycle-7 `no-os-imports-in-domain-packages` sweep. Benign usage (env vars for subprocess-less orchestration), no `os.path`/separator concat. | Owner's call — file predates this phase and `eval/` is out of my change scope. | `test_no_os_path_or_separator_concat_in_domain_packages` (1 known failure) |
| — | Info | `spark/walk.py` `*_eval` methods | Internal tree-walk evaluator for PySpark semantics; not Python builtin eval. | n/a | covered by AST sweep (Name-call check) |

## Verified-safe (adversarially tested, no findings)

| Boundary | Evidence |
|---|---|
| FTS5 `MATCH` injection | `q='foo" OR 1=1'` + 9 more vectors (`UNION SELECT`, `NEAR/2`, `AND OR NOT`, NUL, `{name}:` column spec, `*`) — `fts_escape` quotes+doubles; `OR 1=1` never broadens results; NUL → typed `OperationalError`→diagnostic, no crash. `TestFtsInjection` (12 cases). |
| `LIKE` wildcard injection | `%`, `_`, `\` escaped via `ESCAPE '\'` + `_escape_like` in lexical path filters, structural `search_symbols`, domain fallback. `TestLikeEscaping` (5 cases). |
| Snapshot+repository scoping | Two repos, one DB, same-named symbols → each repo's search returns only its own paths; domain facet never surfaces sibling-repo entities. `TestCrossRepositoryIsolation` (3 cases). |
| Federation member isolation | Members resolve their own READY snapshot; shared identities computed read-only, no cross-repo persisted edges (code review + `tests/federation` 47 pass). |
| Loopback-only bind | `AtlasApiServer(host=0.0.0.0|8.8.8.8|example.com|::)` → `ApiError unsafe-bind`. Host-header spoof → 403 `bad-host`. `TestLoopbackBind`, `test_host_header_spoof_rejected`. |
| Read-only surface | PUT/DELETE/PATCH → 404/405/501; POST to GET-only route → 404; only `POST /context` + `POST /impact/analyze` exist (compute-only); `PRAGMA query_only = ON` + `mode=ro` verified live. `TestReadOnlyApiSurface`. |
| Rate limiting | Sliding window per client; 429 + `Retry-After` ≥1; static assets exempt (pre-existing `TestRateLimit`, plus new negative-CL path can't bypass it). |
| Symlink escape / `..` traversal | `O_NOFOLLOW` + `resolve`+`is_relative_to` in `_open_verified`; dir-symlink never descended; cycle (`a→b→a`) → `RuntimeError` now caught → `symlink_escape` warning (was a crash vector). `TestSymlinkSecretAlias.test_symlink_cycle_does_not_crash`. |
| Secret files unindexed | `.env`, `*.pem`, keys, credentials → counted via `secret_count`, never hashed/inventoried — extended to `*.env`/`.envrc` (F3) and to resolved-symlink basenames (F2). |
| Interpolations | Terraform `${…}` → `${dynamic}` (never evaluated); Actions `${{…}}` → `<expr>`/`<secret>` — now covering keys too (F4). |

## Numbers

- New adversarial suite: **71 tests**, `tests/security/test_cycle8_adversarial.py`, all passing.
- Scoped regression: `tests/security` 84+1 known-fail, `tests/api` 66+1 skip,
  `tests/discovery` 41, `tests/domains` 453, `tests/search` 102,
  `tests/federation` 47, `tests/indexing` 93, `tests/contracts` 8,
  `tests/cli` 64, `tests/parsers` 29 — all green otherwise.
- `ruff check` on 12 touched sources + test file: clean.
- `mypy` on 12 touched sources: clean.
- Secret patterns: 23 glob rules in `SECRET_PATTERNS`.
- Subprocess gateways: 2 (git, lsp-spawn) — both authorized, unchanged.

## Known limitations / residual risk

- `eval/impact_truth.py` (F8): pre-existing untracked file violating the
  `import os` sweep — reported, untouched per scope.
- `_RateLimiter` is per-process memory — honest for a single-process local
  API (documented in threat model §API).
- `artifact_path_confined` checks resolve-time containment; `read_file_verified`
  re-verifies at open with `O_NOFOLLOW` — the TOCTOU window is bounded to
  parent-dir swaps on filesystems without `O_NOFOLLOW` on every component
  (standard for this codebase's documented model).
- `.env`-detection is name-based; a `.env`-shaped *content* file named
  `config.txt` is out of model scope (documented, not a bug).
