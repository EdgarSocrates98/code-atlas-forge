# Cycle 8.1 — Security Final Review (Phase 10, lf-0153)

Branch `cycle-8.1-ga-readiness`. **Last-pass audit before GA** — re-verified
every cycle-8 fix and hunted new ground opened by 8.1 features:
`explorer_dist` packaged in the wheel (lf-0148), `explorer_static_root`
resolution via `importlib.resources`, `build_release.sh`, `--verbose`
progress/log channels, and `/api/v1/diagnostics`.

Threat model unchanged: local-first, read-only indexer; repository content
is **untrusted**; the HTTP surface is **loopback-only** with a hostile local
client in scope.

## Cycle-8 findings — re-verified still fixed

| # | Severity | Status |
|---|---|---|
| F1 static `startswith` traversal | **Verified fixed** — `is_relative_to` + resolve; 11 new traversal variants in `TestExplorerDistServing` (encoded `%2e%2e`/`%2f`, `....//`, backslash, `//`, absolute-URI form) |
| F2 benign symlink → secret target | **Verified fixed** — `TestSymlinkSecretAlias` green; extended to dist-asset symlinks (`test_symlinked_dist_file_outside_never_served`, `test_symlink_chain_*`) |
| F3 `.env`-shaped names | **Verified fixed** — `TestEnvNameCoverage` green |
| F4 `${{ secrets.* }}` in YAML keys | **Verified fixed** — `TestCicdExpressionNeverConcrete` green |
| F5 credential-shaped values | **Verified fixed** — `TestSecretShapedValues` green |
| F6 hostile `Content-Length` | **Verified fixed** — `_content_length` typed 400/413; extended to chunked-TE desync test |
| F7 adapter path containment | **Verified fixed** — `TestAdapterPathContainment` green |
| F8 `eval/impact_truth.py` `import os` | **Resolved elsewhere in 8.1** — file no longer imports `os`; suite is 85/85 green, zero known-fail |

## New findings — found, fixed, tested

| # | Severity | File:line | Finding | Fix | Test |
|---|---|---|---|---|---|
| N1 | **Medium** | `observability/logging.py`, `api/server.py`, `cli.py` | Control-char/ANSI injection into stderr: raw request line (`GET /\x1b[31m`, `GET /x\nINJECTED`) flowed verbatim through `_AtlasHttpd.log` (verbose access log) and `log_if_slow` detail (`handler.path`) — forged log lines and terminal escape sequences. Same class in CLI: filenames with `\n` (POSIX-legal) reached `index-plan`/`index failed:`/context-expand human output. | `escape_control()` (C0/C1/DEL → `\xNN`) applied in `_redact_text` (covers every logger incl. `exc_text` pre-render), `_AtlasHttpd.log`, `_index_progress`, `_emit_page`, `_emit_error`, and all human-output sinks carrying repo-derived strings. | `TestLogInjection` (7) + sink tests (`test_escape_control_unit`, `test_redaction_filter_escapes_{msg_and_args,exc_text}`, `test_httpd_log_escapes_request_line`, `test_verbose_api_request_with_ansi_escaped`, `test_index_progress_escapes_hostile_fields`, `test_rst_storm_no_traceback_flood`, `TestVerboseProgressHonest`) |
| N2 | Low | `api/server.py` `_dispatch` | Hostile request-target raised unhandled `ValueError` → 500 `internal`: NUL byte in `resolve()` (`GET /a\x00b`), malformed IPv6 absolute-URI (`GET http://[`). Client error mislabeled as server error. | `except ValueError` → typed 400 `bad-request` (also converts `int()` failures in POST bodies from 500 to honest 400). | `test_nul_byte_in_path_is_typed_400`, `test_malformed_absolute_uri_is_typed_400` |
| N3 | Low | `api/server.py` `_serve_static` | Asset size checked **after** `read_bytes()` — a planted >4 MiB file in dist was fully loaded into memory before rejection. | `stat().st_size` gate before the read; post-read check kept as belt. | `test_oversized_asset_rejected_before_full_read` |
| N4 | Low | `api/server.py` `_AtlasHandler` | `Server: BaseHTTP/0.6 Python/3.12.3` fingerprinted the runtime stack on every response (incl. stdlib error pages). | `server_version="code-atlas-api"`, `sys_version=""`. | `test_server_header_no_stack_fingerprint` |
| N5 | Low | `scripts/build_release.sh` | `SOURCE_DATE_EPOCH` from env reached `date`/`int()`/zip metadata unvalidated (non-numeric → late crash w/ traceback mid-pipeline); `VER` regex output entered artifact names unvalidated. | Early digit-only `case` guard for SDE (before npm/build); PEP 440-charset guard for `VER`. | `TestBuildReleaseScript` (5) |
| N6 | Low | `api/server.py` `_AtlasHttpd` | `socketserver.handle_error` printed a full traceback **per connection RST** — a loopback client floods stderr at will (log-flood DoS on the operator channel). | `handle_error` suppresses `ConnectionError`/`TimeoutError` transport noise; real handler bugs keep default traceback. | `test_rst_storm_no_traceback_flood` |

## Verified-safe — new ground, no findings

| Boundary | Evidence |
|---|---|
| `explorer_dist` static serving | `is_relative_to` containment + suffix allowlist {html,js,css,json,svg,png,ico,woff,woff2,txt,map}; `..`/encoded/backslash/absolute-URI traversal → SPA fallback or 404, never target bytes. Dist-file symlink → outside → resolve leaves root → fallback (never serves). Symlink → inside → served (legit). Whole `static_root` symlinked → allowlist still blocks non-listed files. |
| `Range`/`If-Modified-Since`/`If-None-Match`/`If-Range` | Never read — always full-200; `Accept-Ranges`/`ETag`/`Last-Modified`/`Content-Range` never emitted (consistent with `Cache-Control: no-store`). |
| `explorer_static_root` | Fixed resource names only (`explorer_dist`, `explorer/dist/…`) — no request/env input reaches `importlib.resources`; packaged dist always wins over repo hint; `index.html` required; loaders without real `Path` → fallback. `TestExplorerStaticRootResolution` (4). |
| Header DoS | stdlib bounds proven live: >100 headers → 431, header line >64 KiB → 431, request line >64 KiB → 414. Colon-less header line → bounded response, no hang. `TestRequestDosBounds` (7). |
| Slow-loris / half-open flood | `request_timeout` socket deadline reaps idle request lines (~1s in test); 16 half-open connections don't starve the threaded server. |
| `Transfer-Encoding: chunked` | Not decoded (no CL → body=`{}`) → typed 400, no desync, no read of smuggled bytes. |
| `--verbose` progress channel | Events are `stage`/`parse-progress`/`noop` with fixed keys + counters — hostile filename `evil\n\x1b[31m.py` indexed end-to-end emits only `[index]`-prefixed lines. |
| `scripts/build_release.sh` | `ROOT` anchored to `BASH_SOURCE` (foreign cwd can't spoof); args allowlist; `set -eu -o pipefail`; all paths quoted. |

## CSP — co-evolved with the Angular migration

Verified current `_security_headers` state — **the migration already
dropped `script-src-attr`; nothing to remove**:

```
default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';
img-src 'self' data:; connect-src 'self'; font-src 'self';
worker-src 'self'; object-src 'none'; base-uri 'self';
form-action 'self'; frame-ancestors 'none'
```

- `script-src 'self'` — no `'unsafe-inline'`, no `'unsafe-eval'`, no
  `script-src-attr` directive at all. Regression-pinned by
  `TestCspPostMigration`.
- `style-src 'unsafe-inline'` — **kept intentionally**: the Angular dist
  inlines critical CSS (`data-beasties-container` `<style>` in
  `index.html`); dropping it breaks first paint. Style ≠ script — CSS
  injection on a loopback SPA can't execute JS under this CSP.
- Packaged `explorer_dist/index.html` verified: every `<script>` tag is
  `src=` external — the CSP claim is true of the real artifact, not just
  the header.
- CSP emitted on success **and** error responses (API + static).

## `/api/v1/diagnostics` — info-disclosure decision

The endpoint exposes `database.path` (absolute path), `root_handle` /
`identity_key` (workspace paths), snapshot/analyzer metadata, runtime
versions. **Accepted for GA** — rationale:

1. Bind is loopback-only; the reader is the same OS user who launched
   the tool and already knows these paths (the CLI prints them).
2. Cross-origin exfiltration is structurally blocked: foreign `Origin` →
   403 (`bad-origin`); `Host` allowlist defeats DNS-rebinding; no CORS
   allow ever crosses origins; GETs are side-effect-free, so a malicious
   web page can trigger a request but cannot read the response.
3. No secrets in the payload: `observability` is key-allowlisted
   (`log_level`/`log_format`/`slow_ms` only); `adapters_error` is bounded
   to 200 chars; `last_job` carries only id/status/timestamps/stage_ms —
   tested that arbitrary env vars never leak.

If the API is ever exposed beyond loopback (a deliberate future decision,
not a drift), `database.path`/`root_handle` must be re-reviewed.

## Secret-redaction sweep — new paths

- `RedactionFilter` now escapes control chars in `msg`, `args` **and**
  `exc_text` (pre-rendered traceback) — filename `a\nb.py` in an
  exception can no longer forge log lines.
- `log_if_slow` detail (raw request path) → escaped via the filter.
- `_AtlasHttpd.log` (verbose access line) → escaped.
- Diagnostics fields: allowlisted keys, bounded error strings, no env
  echo — asserted by `TestDiagnosticsDisclosure`.
- Token/PEM/home redaction unchanged — existing
  `tests/observability` suite green.

## Test inventory

| Suite | Tests | Result |
|---|---:|---|
| `tests/security/test_cycle8_adversarial.py` | 71 | green |
| `tests/security/test_domain_security.py` | 14 | green |
| `tests/security/test_cycle81_security_final.py` (new) | 62 | green |
| **security total** | **147** | **all green** |
| `tests/observability` | 24 | green |
| `tests/api` | 91 | green (1 pre-existing skip) |
| `tests/cli` | 68 | green |

- `ruff check` on all touched files: clean. `mypy` strict on
  `logging.py`/`server.py`/`cli.py`: clean. `bash -n` on
  `build_release.sh`: clean.
- One test-infrastructure note: `AtlasApiServer.shutdown()` blocks when
  `serve_forever` never ran — the new suite uses `server_close()` there.

## Residual risk statement for GA

- **Build-host trust**: `build_release.sh` executes `npm ci`, `npm run
  build`, `python -m build` from the maintainer's PATH — a poisoned
  build environment is out of model (same as any release pipeline).
  Env inputs that *travel into artifacts* (`SOURCE_DATE_EPOCH`, `VER`)
  are now charset-validated (N5).
- **Duplicate `Content-Length`**: first header wins; leftover bytes hit
  the next-request parser and 400. No upstream proxy exists on the
  loopback path — smuggling is self-DoS only. Declared, not exploitable.
- **`*.map` source maps** are inside the shipped dist and share its
  suffix allowlist — serving them is part of the package content, same
  trust as the JS itself.
- **Static assets exempt from rate limit** — documented since cycle-6;
  bounded by suffix allowlist + `MAX_RESPONSE` + loopback-only.
- **TOCTOU** on dist file swap between `stat()`/`is_file()` and
  `read_bytes()` — bounded to local fs-write attackers who could already
  modify the dist.
- **JSON mode outputs raw data** (correct — escaping is a terminal
  concern); terminals consuming `--json` output must treat fields as
  untrusted text, as always.
- F8 note: `eval/impact_truth.py` was fixed elsewhere in 8.1 — the
  cycle-8 "reported-not-fixed" register is now empty.

**Verdict**: 6 new findings (1 medium, 5 low), all fixed and
regression-pinned in this phase; zero unresolved vulnerabilities
blocking GA; residual risks are declared design properties of a
loopback-only local tool.
