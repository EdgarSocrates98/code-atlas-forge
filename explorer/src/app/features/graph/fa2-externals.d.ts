/**
 * Minimal typings for the untyped CJS subpaths of
 * graphology-layout-forceatlas2 consumed by the async layout pipeline.
 * The library ships only index.d.ts/worker.d.ts; these declarations cover
 * exactly what `fa2.worker.ts` / `fa2-layout-runner.ts` /
 * `graph-canvas.component.ts` import — nothing more is promised.
 */

declare module 'graphology-layout-forceatlas2/iterate' {
  /**
   * One ForceAtlas2 iteration over the flat matrices produced by
   * `helpers.graphToByteArrays`. Mutates `nodes`/`edges` in place.
   * `settings` must be complete (defaults already merged).
   */
  function iterate(
    settings: Record<string, unknown>,
    nodes: Float32Array,
    edges: Float32Array,
  ): void;
  export default iterate;
}

declare module 'graphology-layout-forceatlas2/defaults' {
  /** The library's DEFAULT_SETTINGS table. */
  const DEFAULT_SETTINGS: Record<string, unknown>;
  export default DEFAULT_SETTINGS;
}

declare module 'graphology-layout-forceatlas2/helpers' {
  /** Serialized node/edge matrices (PPN=10, PPE=3 float32 records). */
  export interface Fa2Matrices {
    nodes: Float32Array;
    edges: Float32Array;
  }

  /** Structural subset of graphology's Graph the serializer reads. */
  export interface Fa2SerializableGraph {
    forEachNode(
      cb: (node: string, attr: Record<string, unknown>) => void,
    ): void;
    forEachEdge(
      cb: (
        edge: string,
        attr: Record<string, unknown>,
        source: string,
        target: string,
        sourceAttr: Record<string, unknown>,
        targetAttr: Record<string, unknown>,
        undirected: boolean,
      ) => void,
    ): void;
  }

  export function graphToByteArrays(
    graph: Fa2SerializableGraph,
    getEdgeWeight: (
      edge: string,
      attr: Record<string, unknown>,
      source: string,
      target: string,
      sourceAttr: Record<string, unknown>,
      targetAttr: Record<string, unknown>,
      undirected: boolean,
    ) => number,
  ): Fa2Matrices;
}
