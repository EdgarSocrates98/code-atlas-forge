/**
 * Kills the real `code-atlas explorer` subprocess started by
 * `global-setup.ts`. Uses the stashed pid (env var) rather than module
 * state, since Playwright does not guarantee setup/teardown share a module
 * instance across worker processes.
 */
export default async function globalTeardown(): Promise<void> {
  const pid = process.env['ATLAS_E2E_PID'];
  if (!pid) return;
  try {
    process.kill(Number(pid), 'SIGTERM');
  } catch {
    // already gone — fine, nothing to clean up.
  }
}
