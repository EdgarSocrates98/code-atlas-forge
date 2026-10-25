import { spawn, ChildProcess, execFileSync } from 'node:child_process';
import { mkdtempSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

/**
 * Real E2E harness, no mocks: indexes the repo fixture with the real
 * `code-atlas` CLI into an isolated workspace (`CODE_ATLAS_DATA_DIR`), then
 * boots `code-atlas explorer` as a real subprocess serving the compiled
 * Angular build + the real HTTP API over loopback.
 *
 * Playwright's `webServer` option can't express "run an indexer, then boot
 * a server with the resulting DB", so this global setup does both and
 * stashes handles via env vars; `global-teardown.ts` kills the explorer
 * process.
 */

const REPO_ROOT = join(__dirname, '..', '..');
const FIXTURE_REPO = join(REPO_ROOT, 'tests', 'fixtures', 'sample_repo');
const PORT = Number(process.env['ATLAS_E2E_PORT'] ?? 8743);

let explorerProc: ChildProcess | undefined;

function pythonBin(): string {
  if (process.env['ATLAS_PYTHON']) return process.env['ATLAS_PYTHON'];
  // venv local do repo — code_atlas não está no python3 do sistema
  const venv = join(REPO_ROOT, '.venv', 'bin', 'python');
  if (existsSync(venv)) return venv;
  return 'python3';
}

export default async function globalSetup(): Promise<void> {
  const dataDir = mkdtempSync(join(tmpdir(), 'atlas-e2e-'));
  process.env['ATLAS_E2E_DATA_DIR'] = dataDir;
  process.env['ATLAS_E2E_BASE_URL'] = `http://127.0.0.1:${PORT}`;

  const env = {
    ...process.env,
    CODE_ATLAS_DATA_DIR: dataDir,
  };

  if (!existsSync(FIXTURE_REPO)) {
    throw new Error(`fixture repo not found at ${FIXTURE_REPO}`);
  }

  // 1. Index the fixture repo with the real CLI (module invocation — no
  //    dependency on a global `code-atlas` being on PATH).
  execFileSync(pythonBin(), ['-m', 'code_atlas', 'index', FIXTURE_REPO], {
    cwd: REPO_ROOT,
    env,
    stdio: 'inherit',
  });

  // 2. Boot the real explorer (API + compiled Angular dist) as a subprocess.
  explorerProc = spawn(
    pythonBin(),
    ['-m', 'code_atlas', 'explorer', '--port', String(PORT)],
    { cwd: REPO_ROOT, env, stdio: 'inherit' },
  );

  // Give the ThreadingHTTPServer a moment to bind before tests hit it.
  await waitForServer(`http://127.0.0.1:${PORT}/api/v1/repositories`, 15_000);

  // Stash pid for teardown (Playwright reloads modules per worker, so we
  // also persist it in an env var rather than relying on module state).
  if (explorerProc.pid) {
    process.env['ATLAS_E2E_PID'] = String(explorerProc.pid);
  }
}

async function waitForServer(url: string, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let lastErr: unknown;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url);
      // Any HTTP response (even 4xx) means the server is up.
      if (res.status) return;
    } catch (err) {
      lastErr = err;
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(
    `explorer did not come up at ${url} within ${timeoutMs}ms: ${String(lastErr)}`,
  );
}
