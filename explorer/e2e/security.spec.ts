import { test, expect, request } from '@playwright/test';

/**
 * Hardening checks (Phase 17) against the real server in
 * `src/code_atlas/api/server.py`, exercised as a black box over real HTTP —
 * no unit-testing the Python module here, just the wire behavior a browser
 * or attacker would see. Uses Playwright's `APIRequestContext` directly
 * (bypassing the browser) so we can send malformed `Host`/`Origin` headers
 * and raw bytes that a real browser would normalize away.
 */

const BASE = process.env['ATLAS_E2E_BASE_URL'] ?? 'http://127.0.0.1:8743';

test.describe('security hardening', () => {
  test('bad Host header is rejected with 403', async () => {
    const ctx = await request.newContext();
    const res = await ctx.fetch(`${BASE}/api/v1/repositories`, {
      headers: { Host: 'evil.example.com' },
    });
    expect(res.status()).toBe(403);
    const body = await res.json();
    expect(body.error?.code).toBe('bad-host');
    await ctx.dispose();
  });

  test('foreign Origin is rejected with 403 (anti-CSRF/CORS)', async () => {
    const ctx = await request.newContext();
    const res = await ctx.fetch(`${BASE}/api/v1/repositories`, {
      headers: { Origin: 'https://attacker.example.com' },
    });
    expect(res.status()).toBe(403);
    const body = await res.json();
    expect(body.error?.code).toBe('bad-origin');
    await ctx.dispose();
  });

  test('request without Origin is allowed (local tooling / curl)', async () => {
    const ctx = await request.newContext();
    const res = await ctx.fetch(`${BASE}/api/v1/repositories`);
    expect(res.status()).toBe(200);
    await ctx.dispose();
  });

  test('oversized POST body is rejected with 413', async () => {
    const ctx = await request.newContext();
    const oversized = 'x'.repeat(70 * 1024); // > MAX_BODY (64 KiB)
    const res = await ctx.fetch(`${BASE}/api/v1/context`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      data: JSON.stringify({ task: oversized }),
    });
    expect(res.status()).toBe(413);
    await ctx.dispose();
  });

  test('path traversal in static asset path is not served', async () => {
    const ctx = await request.newContext();
    const res = await ctx.fetch(`${BASE}/%2e%2e/%2e%2e/%2e%2e/etc/passwd`);
    // Server either 404s the attempt or falls back to the SPA index —
    // either way it must never leak a file outside the static root.
    expect([200, 404]).toContain(res.status());
    if (res.status() === 200) {
      const text = await res.text();
      expect(text).not.toMatch(/root:.*:0:0:/);
    }
    await ctx.dispose();
  });

  test('no Set-Cookie is ever sent', async () => {
    const ctx = await request.newContext();
    const res = await ctx.fetch(`${BASE}/api/v1/repositories`);
    expect(res.headers()['set-cookie']).toBeUndefined();
    await ctx.dispose();
  });

  test('security headers present on API responses', async () => {
    const ctx = await request.newContext();
    const res = await ctx.fetch(`${BASE}/api/v1/repositories`);
    const headers = res.headers();
    expect(headers['x-content-type-options']).toBe('nosniff');
    expect(headers['cache-control']).toBe('no-store');
    expect(headers['referrer-policy']).toBe('no-referrer');
    await ctx.dispose();
  });

  test('CORS preflight (OPTIONS) with foreign Origin is rejected', async () => {
    const ctx = await request.newContext();
    const res = await ctx.fetch(`${BASE}/api/v1/repositories`, {
      method: 'OPTIONS',
      headers: { Origin: 'https://attacker.example.com' },
    });
    expect(res.status()).toBe(403);
    await ctx.dispose();
  });
});
