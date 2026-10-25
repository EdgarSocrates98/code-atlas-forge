# Explorer security hardening (Cycle 6, Phase 17)

Scope: `src/code_atlas/api/server.py` — the stdlib `http.server` process that
serves both the JSON API (`/api/v1/*`) and the compiled Angular static
assets for `code-atlas explorer`. This document records the threat model,
the guards that exist in the code today, and which of them are exercised by
automated tests versus read-only code review.

This document does not change `src/code_atlas/api/server.py`. It is an audit
of what's there plus the test coverage added in `explorer/e2e/security.spec.ts`.

## Threat model

The server binds to loopback only and is meant to be reached by:

- the Explorer SPA running in a browser tab on the same machine,
- local CLI/MCP tooling (`curl`, scripts, agent harnesses).

It is explicitly **not** meant to be reached by:

- other machines on the network (no `0.0.0.0` bind),
- a malicious web page open in another tab on the same machine
  (DNS-rebinding, CSRF-style cross-origin fetch),
- a browser extension or script trying to pivot through the API to read
  arbitrary files off disk.

The server is read-only by contract (`PRAGMA query_only = ON`, no write
handlers), which limits the blast radius of anything that does get through,
but does not make the surface above moot — index contents (symbol names,
file paths, snippets) are still sensitive to local repos with secrets in
source, and the static file server is a classic path-traversal target.

## Guards present in `server.py` and how they're verified

| Guard | Where | Verified by |
|---|---|---|
| Loopback-only bind (`127.0.0.1`/`localhost`/`::1`, rejects anything else at construction) | `AtlasApiServer.__init__` | Code review only — can't test a refused bind without also testing the refusal, which is a unit-test concern (`tests/`), not E2E. Not exercised by Playwright. |
| `Host` header allowlist (anti DNS-rebinding) — non-matching `Host` → `403 bad-host` | `_AtlasHandler._guard` | `security.spec.ts` → "bad Host header is rejected with 403" |
| `Origin` allowlist — foreign `Origin` → `403 bad-origin`; **no** `Origin` header is allowed (so local `curl`/tooling keeps working) | `_AtlasHandler._origin_ok` / `_guard` | `security.spec.ts` → "foreign Origin is rejected", "request without Origin is allowed" |
| CORS preflight (`OPTIONS`) also enforces the Origin allowlist before replying `204` | `do_OPTIONS` | `security.spec.ts` → "CORS preflight with foreign Origin is rejected" |
| Request body size cap (`MAX_BODY` = 64 KiB) → `413 payload-too-large` | `_AtlasHandler._body` | `security.spec.ts` → "oversized POST body is rejected with 413" |
| Response size cap (`MAX_RESPONSE` = 4 MiB), downgrades to a typed 500 instead of streaming an unbounded body | `_json` | Not exercised — would need a pathological index large enough to produce a >4 MiB single response; out of scope for this E2E pass. Flagged as a gap below. |
| Static asset path traversal: resolves to an absolute path and requires it to stay under `static_root`, else falls back to `index.html`; unknown extensions → `404` | `_serve_static` | `security.spec.ts` → "path traversal in static asset path is not served" |
| No cookies ever set | whole handler — no `Set-Cookie` anywhere | `security.spec.ts` → "no Set-Cookie is ever sent" |
| `X-Content-Type-Options: nosniff`, `Cache-Control: no-store`, `Referrer-Policy: no-referrer` on every response | `_security_headers` | `security.spec.ts` → "security headers present on API responses" |
| Typed errors only (`atlas-error` shape), no traceback ever reaches the client | `_error` | Code review — the `except Exception` branch truncates `str(e)` to 200 chars and never includes `traceback`. Not independently E2E-tested (would require forcing an unhandled exception deliberately). |
| Read-only DB connection (`read_only=True` + `PRAGMA query_only = ON`) | `_AtlasHttpd.connect` | Code review only — this is a data-integrity guard more than a wire-level one; no HTTP-level test can prove a write was *rejected* without an endpoint that would otherwise write, and none exist. |

## Gaps found during this audit (not fixed — out of scope per task boundary)

1. **No `Content-Security-Policy` header.** The response headers set by
   `_security_headers` are `X-Content-Type-Options`, `Cache-Control`, and
   `Referrer-Policy` — there is no `Content-Security-Policy` or
   `X-Frame-Options` header anywhere in `server.py`. For a page that's only
   ever `fetch()`-ed as JSON this matters less, but the *static* HTML
   response (`index.html`) is served by the same handler and currently ships
   with no CSP, so a script-injection bug in the Angular app (e.g. via an
   unsanitized evidence string rendered into the DOM) has no defense-in-depth
   from the server. Recommend adding a conservative
   `Content-Security-Policy: default-src 'self'` to `_serve_static` and to
   `_json` as a follow-up spec — not done here since it's `src/code_atlas`
   production code, out of this task's scope.
2. **Response size cap is untested.** `MAX_RESPONSE` degrades to a 500
   instead of truncating silently, which is the right failure mode, but
   there's no automated test (unit or E2E) proving it actually fires. Would
   require a synthetic fixture large enough to blow past 4 MiB in one
   response — worth a dedicated unit test in `tests/api/` rather than E2E.
3. **`X-Frame-Options` / `frame-ancestors`** absent — low risk given
   loopback-only bind + Origin allowlist blocks cross-origin `fetch`, but a
   same-origin `<iframe>` clickjacking vector via a locally-served malicious
   HTML file is not addressed by Origin checks (file:// origins don't send
   `Origin` and would be allowed through `_origin_ok`'s "no Origin → true"
   path for the *API*, though the attacker would still need to defeat the
   `Host` check for API calls, and the static shell loaded via `file://`
   isn't the same page as the real Explorer anyway). Noted for the
   `src/code_atlas` owners, not fixed here.

## Test file

`explorer/e2e/security.spec.ts` — 8 cases, run via `npx playwright test
security.spec.ts` against the real running server (see
`docs/cycle-6-e2e.md` for execution details and results).
