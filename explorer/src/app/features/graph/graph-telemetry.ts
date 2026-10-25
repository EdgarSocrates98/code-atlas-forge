/**
 * Cycle-8 hardening dev hook: live-structure counters and handles
 * exposed at `window.__atlasGraph`.
 *
 * What it answers honestly:
 * - `sigmaLive` / `workersLive` — renderer instances and FA2 workers
 *   alive right now. They must return to baseline when the graph screen
 *   is destroyed; a residue is a real leak, not a heuristic.
 * - `runs` — every layout run started and how it terminated. Invariant:
 *   `started === done + cancelled + stale + error` once pending resolves
 *   land; a gap means a run never settled (the zombie-worker bug this
 *   hook caught: done/error runs used to leave their worker alive).
 * - `fallbacks` — how often the main-thread engine had to take over
 *   (Worker unavailable, spawn refused, or startup error).
 *
 * The `sigma`/`workers` handles are live object references so e2e can
 * read true renderer state (node positions → viewport edge midpoints)
 * and simulate a hard worker kill via `terminate()`. The hook mutates
 * nothing by itself; it is a read surface plus counters.
 */

export interface GraphRunCounters {
  started: number;
  done: number;
  cancelled: number;
  stale: number;
  error: number;
}

export interface AtlasGraphTelemetry {
  sigmaLive: number;
  sigmaTotal: number;
  workersLive: number;
  workersTotal: number;
  /** Runs that degraded to the main-thread engine. */
  fallbacks: number;
  /** Last layout failure text surfaced to the user, if any. */
  lastLayoutError: string | null;
  runs: GraphRunCounters;
  /** Live Sigma instance (at most one canvas exists at a time). */
  sigma: unknown;
  /** Live FA2 worker handles — e2e uses these to hard-kill a run. */
  workers: Set<Worker>;
}

declare global {
  interface Window {
    __atlasGraph?: AtlasGraphTelemetry;
  }
}

let telemetry: AtlasGraphTelemetry | null = null;

/** The singleton hook — created on first use, never reset. */
export function graphTelemetry(): AtlasGraphTelemetry {
  if (telemetry) return telemetry;
  telemetry = {
    sigmaLive: 0,
    sigmaTotal: 0,
    workersLive: 0,
    workersTotal: 0,
    fallbacks: 0,
    lastLayoutError: null,
    runs: { started: 0, done: 0, cancelled: 0, stale: 0, error: 0 },
    sigma: null,
    workers: new Set<Worker>(),
  };
  if (typeof window !== 'undefined') {
    window.__atlasGraph = telemetry;
  }
  return telemetry;
}

export function trackSigma(instance: unknown): void {
  const t = graphTelemetry();
  t.sigmaLive += 1;
  t.sigmaTotal += 1;
  t.sigma = instance;
}

export function untrackSigma(instance: unknown): void {
  const t = graphTelemetry();
  t.sigmaLive = Math.max(0, t.sigmaLive - 1);
  if (t.sigma === instance) t.sigma = null;
}

export function trackWorker(worker: Worker): void {
  const t = graphTelemetry();
  t.workers.add(worker);
  t.workersLive += 1;
  t.workersTotal += 1;
}

export function untrackWorker(worker: Worker): void {
  const t = graphTelemetry();
  if (t.workers.delete(worker)) {
    t.workersLive = Math.max(0, t.workersLive - 1);
  }
}

export function recordRunStarted(): void {
  graphTelemetry().runs.started += 1;
}

export function recordRunFinished(status: 'done' | 'cancelled' | 'stale' | 'error'): void {
  graphTelemetry().runs[status] += 1;
}

/** The worker engine degraded to the main-thread chunked engine. */
export function recordFallback(): void {
  graphTelemetry().fallbacks += 1;
}

export function recordLayoutError(message: string | null): void {
  graphTelemetry().lastLayoutError = message;
}
