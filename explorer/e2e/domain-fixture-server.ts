import { spawn, ChildProcess, execFileSync } from 'node:child_process';
import { mkdtempSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, join } from 'node:path';
import { createServer } from 'node:net';

/**
 * Dedicated explorer instance for the cycle-7 domain views
 * (`/lineage`, `/contracts`, `/infra`, `/pipelines`, `/federation`).
 *
 * Why not the shared suite server? `global-setup.ts` indexes only
 * `tests/fixtures/sample_repo`, which carries no domain data — the
 * domain views would render their empty states and the audit would
 * never touch a populated table/card. Indexing the domain fixtures
 * into the shared data dir is not an option either: other specs
 * hard-assert the single-repo state (`flows2.spec.ts` expects exactly
 * one `.repo-card`), so this helper boots a *second* real
 * `code-atlas explorer` subprocess on its own port with its own
 * `CODE_ATLAS_DATA_DIR`. Same real CLI, real index, real HTTP API —
 * just isolated.
 *
 * Indexed here (all with the real `code-atlas index --domains …`):
 *   tests/fixtures/sql_warehouse    → --domains sql       → /lineage
 *   tests/fixtures/terraform_infra  → --domains terraform → /infra
 *   tests/fixtures/cicd_repo        → --domains cicd      → /pipelines
 *   tests/fixtures/api_repo         → --domains api       → /contracts
 *   tests/fixtures/sample_repo      → (no domain data)    → empty states
 * Plus `code-atlas federation create e2e-ws` + two `federation add`
 * members so `/federation` renders a real workspace + members table.
 */

const REPO_ROOT = join(__dirname, '..', '..');

const FIXTURES = {
  sql: 'tests/fixtures/sql_warehouse',
  terraform: 'tests/fixtures/terraform_infra',
  cicd: 'tests/fixtures/cicd_repo',
  api: 'tests/fixtures/api_repo',
  sample: 'tests/fixtures/sample_repo',
} as const;

const DOMAIN_FLAGS: Record<keyof typeof FIXTURES, string | null> = {
  sql: 'sql',
  terraform: 'terraform',
  cicd: 'cicd',
  api: 'api',
  sample: null,
};

export interface DomainFixtureServer {
  /** Base URL of the dedicated explorer (e.g. http://127.0.0.1:8744). */
  readonly baseUrl: string;
  /** Repository id per fixture key, resolved from GET /api/v1/repositories. */
  readonly repos: Record<keyof typeof FIXTURES, number>;
  /** Workspace name created for /federation (single workspace → auto-select). */
  readonly workspace: string;
  /** Temp data dir backing the instance (for diagnostics only). */
  readonly dataDir: string;
  /** Kills the explorer subprocess (SIGTERM). */
  stop(): void;
}

function pythonBin(): string {
  if (process.env['ATLAS_PYTHON']) return process.env['ATLAS_PYTHON'];
  const venv = join(REPO_ROOT, '.venv', 'bin', 'python');
  if (existsSync(venv)) return venv;
  return 'python3';
}

function cli(args: string[], env: NodeJS.ProcessEnv): void {
  execFileSync(pythonBin(), ['-m', 'code_atlas', ...args], {
    cwd: REPO_ROOT,
    env,
    stdio: 'inherit',
  });
}

/** Reserve an ephemeral loopback port, then release it for the server. */
function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = createServer();
    srv.once('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const address = srv.address();
      const port = typeof address === 'object' && address ? address.port : 0;
      srv.close(() => resolve(port));
    });
  });
}

async function waitForServer(url: string, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let lastErr: unknown;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url);
      if (res.status) return;
    } catch (err) {
      lastErr = err;
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(
    `domain-fixture explorer did not come up at ${url} within ${timeoutMs}ms: ${String(lastErr)}`,
  );
}

/**
 * Indexes the domain fixtures, creates a real federation workspace and
 * boots the explorer subprocess. Takes ~15-30 s — call from beforeAll
 * with an explicit `test.setTimeout(...)`, never per-test.
 */
export async function bootDomainFixtureServer(): Promise<DomainFixtureServer> {
  const dataDir = mkdtempSync(join(tmpdir(), 'atlas-domain-e2e-'));
  const env = { ...process.env, CODE_ATLAS_DATA_DIR: dataDir };

  for (const [key, relPath] of Object.entries(FIXTURES)) {
    const fixture = join(REPO_ROOT, relPath);
    if (!existsSync(fixture)) {
      throw new Error(`domain fixture not found at ${fixture}`);
    }
    const domains = DOMAIN_FLAGS[key as keyof typeof FIXTURES];
    cli(['index', fixture, ...(domains ? ['--domains', domains] : [])], env);
  }

  // Real federation state: one workspace with two members (the CLI
  // `view` requires >= 2; the UI auto-selects a single workspace).
  const workspace = 'e2e-ws';
  cli(['federation', 'create', workspace], env);
  cli(['federation', 'add', workspace, join(REPO_ROOT, FIXTURES.sql)], env);
  cli(['federation', 'add', workspace, join(REPO_ROOT, FIXTURES.sample)], env);

  const port = await freePort();
  const baseUrl = `http://127.0.0.1:${port}`;
  const proc: ChildProcess = spawn(
    pythonBin(),
    ['-m', 'code_atlas', 'explorer', '--port', String(port)],
    { cwd: REPO_ROOT, env, stdio: 'inherit' },
  );
  let stopped = false;
  proc.on('exit', () => {
    stopped = true;
  });

  try {
    await waitForServer(`${baseUrl}/api/v1/repositories`, 15_000);
  } catch (err) {
    proc.kill('SIGTERM');
    throw err;
  }

  // Resolve repository ids once — tests deep-link with ?repo=<id>.
  const res = await fetch(`${baseUrl}/api/v1/repositories`);
  if (!res.ok) {
    proc.kill('SIGTERM');
    throw new Error(`GET /api/v1/repositories failed: ${res.status}`);
  }
  const body = (await res.json()) as {
    items?: { id: number; root_handle: string }[];
  };
  const repos = {} as Record<keyof typeof FIXTURES, number>;
  for (const [key, relPath] of Object.entries(FIXTURES)) {
    const dir = basename(relPath);
    const match = (body.items ?? []).find((r) => r.root_handle.endsWith(dir));
    if (!match) {
      proc.kill('SIGTERM');
      throw new Error(`fixture repo '${dir}' missing from /api/v1/repositories`);
    }
    repos[key as keyof typeof FIXTURES] = match.id;
  }

  return {
    baseUrl,
    repos,
    workspace,
    dataDir,
    stop() {
      if (stopped || !proc.pid) return;
      try {
        proc.kill('SIGTERM');
      } catch {
        /* already gone */
      }
    },
  };
}
