import { defineConfig, devices } from '@playwright/test';

/**
 * Real E2E, no mocks: `global-setup.ts` indexes `tests/fixtures/sample_repo`
 * with the real `code-atlas` CLI and boots the real explorer subprocess
 * (API + compiled Angular dist) on an ephemeral loopback port.
 *
 * Prerequisite: `cd explorer && npm run build` — this suite serves the
 * compiled `dist/explorer/browser` output, not `ng serve`, because that's
 * what `code-atlas explorer` actually serves in production.
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: false, // single shared subprocess — tests share server state
  workers: 1,
  retries: 0,
  reporter: [['list'], ['html', { outputFolder: 'e2e/report', open: 'never' }]],
  globalSetup: require.resolve('./e2e/global-setup.ts'),
  globalTeardown: require.resolve('./e2e/global-teardown.ts'),
  use: {
    baseURL: process.env['ATLAS_E2E_BASE_URL'] ?? 'http://127.0.0.1:8743',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    // Opt-in cross-browser legs (Cycle 8.1 Phase 6, lf-0149): only enabled
    // with ATLAS_E2E_XBROWSER=1 so CI's chromium-only install (`npx
    // playwright install --with-deps chromium` + `npm run e2e`) is
    // unchanged. Scoped to cross-browser.spec.ts — the rest of the suite
    // stays Chromium-only.
    ...(process.env['ATLAS_E2E_XBROWSER']
      ? [
          {
            name: 'firefox',
            use: { ...devices['Desktop Firefox'] },
            testMatch: /cross-browser\.spec\.ts/,
          },
          {
            name: 'webkit',
            use: { ...devices['Desktop Safari'] },
            testMatch: /cross-browser\.spec\.ts/,
          },
        ]
      : []),
  ],
});
