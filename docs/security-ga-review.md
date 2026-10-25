# Security — GA review (cycle-8.1, lf-0153)

Detail doc: `docs/cycle-8.1-security-final.md`. Commit: `d9ef7b9`.

## Verdict

**Zero unresolved production vulnerabilities blocking GA.** All 7
cycle-8 findings re-verified fixed; 6 new findings (1 medium, 5 low)
fixed and regression-pinned in this cycle.

## Cycle-8.1 findings (all fixed)

| # | Sev | Fix |
|---|---|---|
| N1 | Medium | Control-char/ANSI/newline injection into stderr via raw request lines and POSIX filenames — `escape_control()` across all log/human-output sinks |
| N2 | Low | Hostile request-target (NUL byte, malformed IPv6 URI) → unhandled `ValueError` → 500; now typed `400` |
| N3 | Low | Static asset size checked after `read_bytes()` — `stat().st_size` gate pre-read |
| N4 | Low | `Server: BaseHTTP/0.6 Python/3.12.3` fingerprint → `code-atlas-api` |
| N5 | Low | `SOURCE_DATE_EPOCH`/`VER` reached build artifacts unvalidated — digit/PEP 440 guards |
| N6 | Low | `socketserver.handle_error` traceback per RST → log-flood; `ConnectionError`/`TimeoutError` suppressed |

## Verified-safe (no findings)

Explorer dist traversal (11 variants incl. `%2e%2e`), symlink chains,
`Range`/`If-Modified-Since`, `explorer_static_root` precedence,
header-DoS bounds (stdlib 431/414), slow-dribble reaping, chunked-body
no-desync, post-migration CSP (`script-src 'self'` clean;
`style-src 'unsafe-inline'` intentionally kept for Angular bindings).

## Accepted risk

`/api/v1/diagnostics` DB-path disclosure — **ACCEPTED for GA**:
loopback-only, same OS user, foreign `Origin`→403, `Host` allowlist
blocks DNS-rebinding, no CORS. **Re-review mandatory if the API is
ever exposed beyond loopback.**

## Coverage

147 security tests green (71 cycle-8 + 14 domain + 62 new).
`pip-audit` prod: 0. `npm audit --omit=dev`: 0.
