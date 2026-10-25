import iterate from 'graphology-layout-forceatlas2/iterate';

/**
 * ForceAtlas2 worker — Phase 0.2 async layout.
 *
 * Bundled by the Angular/esbuild `new Worker(new URL('./fa2.worker.ts',
 * import.meta.url))` mechanism and served as a same-origin script (the
 * explorer CSP `script-src 'self'` permits it — a Blob worker would be
 * blocked, which is why the shipped FA2LayoutSupervisor is not used).
 *
 * Protocol (main → worker):
 *   { type: 'start', nodes, edges, settings, iterations, progressEvery }
 *   — `nodes`/`edges` are ArrayBuffers of the flat matrices built by
 *     helpers.graphToByteArrays on the main thread, transferred in.
 *
 * Protocol (worker → main):
 *   { type: 'progress', done, total } — every `progressEvery` iterations;
 *   { type: 'done', nodes }           — final matrix, buffer transferred
 *                                       back to the main thread;
 *   { type: 'error', message }        — iterate threw; the run ends.
 *
 * There is no soft-cancel message: cancellation is `worker.terminate()`
 * from the main thread — instant even mid-iteration. Stale-run detection
 * lives in Fa2LayoutRunner; this file is pure compute.
 *
 * `self` is typed through a minimal local scope instead of the
 * `webworker` lib so the file compiles under the app's DOM-only lib set.
 */

interface Fa2StartMessage {
  type: 'start';
  nodes: ArrayBuffer;
  edges: ArrayBuffer;
  settings: Record<string, unknown>;
  iterations: number;
  progressEvery: number;
}

interface Fa2ProgressMessage {
  type: 'progress';
  done: number;
  total: number;
}

interface Fa2DoneMessage {
  type: 'done';
  nodes: ArrayBuffer;
}

interface Fa2ErrorMessage {
  type: 'error';
  message: string;
}

type Fa2WorkerReply = Fa2ProgressMessage | Fa2DoneMessage | Fa2ErrorMessage;

const scope = self as unknown as {
  postMessage(message: Fa2WorkerReply, transfer?: Transferable[]): void;
};

self.onmessage = (event: MessageEvent<Fa2StartMessage>) => {
  const msg = event.data;
  if (!msg || msg.type !== 'start') return;

  const nodes = new Float32Array(msg.nodes);
  const edges = new Float32Array(msg.edges);
  const total = Math.max(0, Math.floor(msg.iterations) || 0);
  const every = Math.max(1, Math.floor(msg.progressEvery) || 1);

  try {
    for (let i = 1; i <= total; i++) {
      iterate(msg.settings, nodes, edges);
      if (i % every === 0 && i < total) {
        scope.postMessage({ type: 'progress', done: i, total });
      }
    }
    scope.postMessage({ type: 'done', nodes: nodes.buffer }, [nodes.buffer]);
  } catch (error) {
    scope.postMessage({
      type: 'error',
      message: error instanceof Error ? error.message : String(error),
    });
  }
};
