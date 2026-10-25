import {
  AfterViewInit,
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  NgZone,
  OnDestroy,
  ViewChild,
  effect,
} from '@angular/core';
import Graph from 'graphology';
import forceAtlas2 from 'graphology-layout-forceatlas2';
import FA2_DEFAULT_SETTINGS from 'graphology-layout-forceatlas2/defaults';
import { graphToByteArrays } from 'graphology-layout-forceatlas2/helpers';
import Sigma from 'sigma';
import {
  FA2_NODE_FIXED_OFFSET,
  FA2_NODE_STRIDE,
  Fa2LayoutRunner,
} from './fa2-layout-runner';
import type { Fa2RunResult } from './fa2-layout-runner';
import { GraphDataStore } from './graph-data.store';
import { GraphMode, GraphEdgeVM, GraphNodeVM } from './graph.models';
import { GraphStateService } from './graph-state.service';
import {
  recordLayoutError,
  recordRunFinished,
  recordRunStarted,
  trackSigma,
  untrackSigma,
} from './graph-telemetry';
import {
  EVIDENCE_MARKERS,
  hashPosition,
  isFinitePosition,
  layoutIterations,
  toEvidenceLevel,
} from './graph.utils';

/* Camera bounds: ratio = fraction of the framed graph visible on screen. */
const MIN_CAMERA_RATIO = 0.02;
const MAX_CAMERA_RATIO = 10;
/** Node must be at least this many px on screen before its label draws. */
const LABEL_ZOOM_THRESHOLD = 8;
const PAN_STEP = 0.08;
const ZOOM_FACTOR = 1.5;
/** Container resize → renderer refresh debounce (ms). */
const RESIZE_DEBOUNCE_MS = 120;

/**
 * Edge weight for layout serialization — mirrors the library default
 * (`'weight'` attribute, falling back to 1). Our edges carry no weight,
 * so every relation pulls equally.
 */
function edgeWeight(
  _edge: string,
  attr: Record<string, unknown>,
): number {
  const w = attr['weight'];
  return typeof w === 'number' && Number.isFinite(w) ? w : 1;
}

interface Palette {
  text: string;
  node: string;
  nodeFile: string;
  nodeSelected: string;
  nodePinned: string;
  nodeFocused: string;
  edgeDim: string;
  statuses: Record<string, string>;
}

/**
 * sigma.js (WebGL) renderer bound to GraphDataStore.
 *
 * Contract with the store:
 * - structure syncs only when `dataVersion` changes;
 * - a layout pass runs only when `dataVersion.needsLayout` is true —
 *   selection, filters and incremental merges never move a node;
 * - computed positions are reported back via `reportPositions` so drill
 *   back/restore keeps the same geometry;
 * - when WebGL is unavailable the store is flagged and the host falls
 *   back to the accessible table view.
 */
@Component({
  selector: 'atlas-graph-canvas',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div
      #stage
      class="graph-stage"
      tabindex="0"
      role="application"
      aria-roledescription="grafo interativo"
      [attr.aria-label]="stageLabel()"
      (keydown)="onKeydown($event)"
    ></div>
    <p id="graph-kbd-help" class="visually-hidden">
      Navegação por teclado: setas movem a câmera; mais e menos controlam o zoom;
      F ajusta a visão; colchetes percorrem os nós; Enter seleciona; E expande o
      nó selecionado; P fixa ou solta o nó; Escape limpa a seleção.
    </p>
  `,
  styles: [
    `
      :host {
        display: block;
        height: 100%;
        min-height: 0;
      }
      .graph-stage {
        width: 100%;
        height: 100%;
        min-height: 320px;
        position: relative;
        background: var(--atlas-color-bg-subtle);
        border-radius: var(--atlas-radius-md);
        outline-offset: -2px;
      }
      .graph-stage:focus-visible {
        outline: 2px solid var(--atlas-color-focus-ring);
      }
    `,
  ],
})
export class GraphCanvasComponent implements AfterViewInit, OnDestroy {
  @ViewChild('stage', { static: true }) private readonly stageRef!: ElementRef<HTMLDivElement>;

  private graph: Graph | null = null;
  private sigma: Sigma | null = null;
  private resizeObserver: ResizeObserver | null = null;
  private themeObserver: MutationObserver | null = null;
  private lastDataVersion = -1;
  private lastRelayoutCount = -1;
  private pendingSync = true;
  private resizeTimer: ReturnType<typeof setTimeout> | null = null;
  private glCanvases: HTMLCanvasElement[] = [];
  private lastMode: GraphMode | null = null;
  /**
   * Async layout lifecycle (Phase 0.2): FA2 iterations run in a Web Worker
   * owned by `layoutRunner`; `layoutGen` is the generation token that makes
   * stale worker results impossible to commit. A run is invalidated by:
   * a newer runLayout (supersede), a dataVersion bump (the serialized node
   * set no longer matches the live graph), an explicit Cancel button, or
   * component destroy.
   */
  private readonly layoutRunner = new Fa2LayoutRunner();
  private layoutGen = 0;
  /**
   * Diagnostics read these live (plain fields, not signals — runLayout runs
   * inside the sync effect where signal writes are deferred in Angular 18).
   * `layoutBusy` now spans the whole async pass: worker spawn, chunked
   * iterations and commit — it is observable for seconds, not microseconds.
   */
  layoutBusy = false;
  /** Live iteration progress of the in-flight run; null when idle. */
  layoutProgress: { done: number; total: number } | null = null;
  lastLayoutMs: number | null = null;
  /**
   * Last layout failure text — cleared when a run commits. The
   * diagnostics panel surfaces it next to "Último layout" so a dead
   * worker/watchdog timeout is visible without watching the toast.
   */
  lastLayoutError: string | null = null;
  private readonly reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  private readonly onMotionChange = (): void => this.applyMotionSettings();
  private readonly onContextLost = (event: Event): void => {
    // preventDefault() lets the browser try to restore the context.
    event.preventDefault();
    this.store.markRendererLost(true);
  };
  private readonly onContextRestored = (): void => {
    this.store.markRendererLost(false);
    this.sigma?.refresh();
  };

  constructor(
    readonly store: GraphDataStore,
    private readonly zone: NgZone,
    private readonly viewStates: GraphStateService,
  ) {
    effect(() => this.sync());
  }

  /* ------------------------------- public ----------------------------- */

  stageLabel(): string {
    const n = this.store.nodes().length;
    const e = this.store.visibleEdges().length;
    return `Grafo com ${n} nós visíveis e ${e} relações. Use colchetes para percorrer os nós.`;
  }

  zoomIn(): void {
    const camera = this.sigma?.getCamera();
    if (!camera) return;
    if (this.motionZero()) {
      camera.setState({ ratio: camera.getState().ratio / ZOOM_FACTOR });
    } else {
      void camera.animatedZoom(ZOOM_FACTOR);
    }
  }

  zoomOut(): void {
    const camera = this.sigma?.getCamera();
    if (!camera) return;
    if (this.motionZero()) {
      camera.setState({ ratio: camera.getState().ratio * ZOOM_FACTOR });
    } else {
      void camera.animatedUnzoom(ZOOM_FACTOR);
    }
  }

  /** Fit the whole graph (camera origin + full extent) — also "reset". */
  fitView(): void {
    this.goTo({ x: 0.5, y: 0.5, ratio: 1, angle: 0 });
  }

  /** Center on one node keeping the current zoom (or zooming in a bit). */
  focusNode(nodeId: string): void {
    if (!this.sigma || !this.graph?.hasNode(nodeId)) return;
    const camera = this.sigma.getCamera();
    const pos = this.sigma.getNodeDisplayData(nodeId);
    if (!pos) return;
    this.goTo({ x: pos.x, y: pos.y, ratio: Math.min(camera.getState().ratio, 0.4) });
  }

  /**
   * Live sigma camera ratio (fraction of the framed graph on screen).
   * Returns null when no renderer exists (table view / WebGL failed) —
   * diagnostics surface that as "—", never an invented number.
   */
  cameraRatio(): number | null {
    const state = this.sigma?.getCamera().getState();
    if (!state || !Number.isFinite(state.ratio) || state.ratio <= 0) return null;
    return state.ratio;
  }

  /** Animate — or snap instantly under prefers-reduced-motion. */
  private goTo(state: { x: number; y: number; ratio: number; angle?: number }): void {
    const camera = this.sigma?.getCamera();
    if (!camera) return;
    if (this.motionZero()) {
      camera.setState(state);
    } else {
      void camera.animate(state, { easing: 'quadraticInOut', duration: 350 });
    }
  }

  /* ------------------------------ lifecycle --------------------------- */

  ngAfterViewInit(): void {
    this.zone.runOutsideAngular(() => {
      try {
        this.initRenderer();
      } catch {
        this.store.markRendererFailed();
      }
    });
  }

  ngOnDestroy(): void {
    // An in-flight layout can never commit after the view dies — kill it
    // before the renderer so its resolve lands on a dead generation.
    this.cancelLayoutRun();
    // Capture the view BEFORE killing the renderer so "navigate away and
    // back" restores the exact camera + selection (see GraphStateService).
    this.persistViewState();
    if (this.resizeTimer !== null) {
      clearTimeout(this.resizeTimer);
      this.resizeTimer = null;
    }
    for (const canvas of this.glCanvases) {
      canvas.removeEventListener('webglcontextlost', this.onContextLost);
      canvas.removeEventListener('webglcontextrestored', this.onContextRestored);
    }
    this.glCanvases = [];
    this.reducedMotion.removeEventListener('change', this.onMotionChange);
    this.themeObserver?.disconnect();
    this.resizeObserver?.disconnect();
    // sigma.kill() releases the WebGL contexts AND cancels sigma's render
    // RAF; the store's AbortController aborts in-flight requests — no
    // callbacks outlive this component.
    const sigma = this.sigma;
    this.sigma = null;
    sigma?.kill();
    if (sigma) untrackSigma(sigma);
    this.graph = null;
  }

  /* ------------------------------ renderer ---------------------------- */

  private initRenderer(): void {
    const container = this.stageRef.nativeElement;
    this.graph = new Graph({ multi: false, type: 'directed' });
    const motion = this.motionZero();
    this.sigma = new Sigma(this.graph, container, {
      renderEdgeLabels: true,
      edgeLabelSize: 11,
      edgeLabelColor: { attribute: 'color' },
      enableEdgeEvents: true,
      hideEdgesOnMove: true,
      hideLabelsOnMove: true,
      labelRenderedSizeThreshold: LABEL_ZOOM_THRESHOLD,
      labelFont: 'ui-sans-serif, system-ui, sans-serif',
      labelSize: 12,
      labelWeight: '500',
      labelColor: { color: this.palette().text },
      edgeLabelFont: 'ui-sans-serif, system-ui, sans-serif',
      defaultEdgeType: 'arrow',
      zIndex: true,
      minCameraRatio: MIN_CAMERA_RATIO,
      maxCameraRatio: MAX_CAMERA_RATIO,
      autoRescale: false,
      autoCenter: false,
      stagePadding: 40,
      enableCameraRotation: false,
      inertiaDuration: motion ? 0 : 200,
      zoomDuration: motion ? 0 : 250,
      doubleClickZoomingDuration: motion ? 0 : 200,
    });
    trackSigma(this.sigma);

    this.sigma.on('clickNode', ({ node }) => {
      this.store.setKbdFocus(node);
      this.store.selectNode(node);
    });
    this.sigma.on('clickEdge', ({ edge }) => this.store.selectEdge(edge));
    this.sigma.on('clickStage', () => this.store.clearSelection());
    this.sigma.on('doubleClickNode', ({ node, event }) => {
      event.preventSigmaDefault();
      const vm = this.store.nodes().find((n) => n.id === node);
      if (vm?.kind === 'file') {
        void this.store.drillFile(vm.path, vm.symbolCount ?? 0, vm.fileId);
      } else {
        void this.store.expandNode(node);
      }
    });

    // WebGL context loss: GPU resets kill every sigma canvas at once.
    // preventDefault() keeps the context restorable; the store flag drives
    // a visible warning so a black canvas is never silent.
    this.glCanvases = Object.values(this.sigma.getCanvases());
    for (const canvas of this.glCanvases) {
      canvas.addEventListener('webglcontextlost', this.onContextLost);
      canvas.addEventListener('webglcontextrestored', this.onContextRestored);
    }

    // Debounced: a burst of resizes (split drag, sidebar toggle) triggers
    // one renderer refresh instead of one per ResizeObserver tick.
    this.resizeObserver = new ResizeObserver(() => {
      if (this.resizeTimer !== null) clearTimeout(this.resizeTimer);
      this.resizeTimer = setTimeout(() => {
        this.resizeTimer = null;
        if (!this.sigma) return;
        this.sigma.resize();
        this.sigma.refresh();
      }, RESIZE_DEBOUNCE_MS);
    });
    this.resizeObserver.observe(container);

    // Theme toggle changes the CSS vars — re-paint when data-theme flips.
    this.themeObserver = new MutationObserver(() => {
      this.sigma?.setSetting('labelColor', { color: this.palette().text });
      this.paintAll();
    });
    this.themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme'],
    });

    this.reducedMotion.addEventListener('change', this.onMotionChange);

    if (this.pendingSync) {
      this.pendingSync = false;
      this.sync();
    }
  }

  private applyMotionSettings(): void {
    if (!this.sigma) return;
    const zero = this.motionZero();
    this.sigma.setSetting('inertiaDuration', zero ? 0 : 200);
    this.sigma.setSetting('zoomDuration', zero ? 0 : 250);
    this.sigma.setSetting('doubleClickZoomingDuration', zero ? 0 : 200);
  }

  private motionZero(): boolean {
    return this.reducedMotion.matches;
  }

  /* ------------------------------- sync ------------------------------- */

  private sync(): void {
    // Track all reactive inputs — Angular records them in this effect.
    const nodes = this.store.nodes();
    const edges = this.store.visibleEdges();
    const dv = this.store.dataVersion();
    const mode = this.store.mode();

    if (!this.graph || !this.sigma) {
      this.pendingSync = true;
      return;
    }

    // Mode switch (arch → file drill): snapshot the outgoing view's camera
    // so leaving mid-drill still preserves the architecture viewport.
    if (this.lastMode !== mode) {
      const outgoing = this.lastMode;
      this.lastMode = mode;
      if (outgoing !== null) this.persistViewState(outgoing);
    }

    if (dv.version !== this.lastDataVersion) {
      this.lastDataVersion = dv.version;
      this.syncStructure(nodes, edges, dv.needsLayout);
    }
    this.paintAll();
  }

  /**
   * Reconciles the graphology graph with the store. Runs the layout pass
   * at most once per data version (needsLayout flag), then freezes
   * positions forever after.
   */
  private syncStructure(nodes: GraphNodeVM[], edges: GraphEdgeVM[], needsLayout: boolean): void {
    const graph = this.graph;
    const sigma = this.sigma;
    if (!graph || !sigma) return;

    // A data revision while a layout is in flight invalidates its matrices
    // (they describe the pre-change node set). Cancel before mutating so a
    // stale worker result can never land on the new structure.
    if (this.layoutRunner.running) this.cancelLayoutRun();

    const wantedNodes = new Set(nodes.map((n) => n.id));
    const wantedEdges = new Set(edges.map((e) => e.id));

    for (const id of [...graph.nodes()]) {
      if (!wantedNodes.has(id)) graph.dropNode(id);
    }
    for (const id of [...graph.edges()]) {
      if (!wantedEdges.has(id)) graph.dropEdge(id);
    }

    // Sanitize before render: NaN/Infinity coordinates corrupt sigma's
    // spatial index for the whole frame. An invalid authored position is
    // replaced by the deterministic hash spot; a node that still cannot
    // be placed is dropped from the canvas (never from the store). Both
    // surface as diagnostics — corrupt geometry is never silent.
    const sanitized: string[] = [];
    const dropped: string[] = [];
    nodes.forEach((vm, i) => {
      const authored = isFinitePosition(vm.x, vm.y);
      if (!authored && (vm.x !== null || vm.y !== null)) sanitized.push(vm.id);
      const pos = authored
        ? { x: vm.x as number, y: vm.y as number }
        : hashPosition(vm.id, i, nodes.length);
      if (!isFinitePosition(pos.x, pos.y)) {
        dropped.push(vm.id);
        return;
      }
      if (!graph.hasNode(vm.id)) {
        graph.addNode(vm.id, { x: pos.x, y: pos.y });
      } else if (authored) {
        // Keep authored positions authoritative (e.g. drill-back restore).
        graph.setNodeAttribute(vm.id, 'x', pos.x);
        graph.setNodeAttribute(vm.id, 'y', pos.y);
      }
    });
    const formatIds = (ids: string[]): string =>
      `${ids.slice(0, 5).join(', ')}${ids.length > 5 ? '…' : ''}`;
    if (sanitized.length) {
      this.store.reportDiagnostics(
        `${sanitized.length} nó(s) com coordenadas inválidas — ` +
          `reposicionados deterministicamente: ${formatIds(sanitized)}`,
      );
    }
    if (dropped.length) {
      this.store.reportDiagnostics(
        `${dropped.length} nó(s) descartados do canvas por coordenadas ` +
          `inválidas: ${formatIds(dropped)}`,
      );
    }

    for (const e of edges) {
      if (graph.hasEdge(e.id)) continue;
      if (!e.from || !e.to) continue; // unresolved target — nothing to draw
      if (!graph.hasNode(e.from) || !graph.hasNode(e.to)) continue;
      try {
        graph.addDirectedEdgeWithKey(e.id, e.from, e.to);
      } catch {
        /* multi-edges/loops are dropped — never fabricate geometry */
      }
    }

    if (needsLayout) {
      this.runLayout();
    }
    // No explicit sigma.refresh() here: paintAll() always runs right after
    // syncStructure inside sync() and ends with the single full refresh —
    // two sync re-indexations per data version was a ~170 ms task at 2000
    // nodes (measured on the relayout click path).
  }

  /**
   * Kicks off one bounded ForceAtlas2 pass in the layout worker — the main
   * thread only serializes the graph, then stays free until the worker's
   * `done` message lands on `finishLayout`. Pinned nodes are serialized
   * with the FA2 `fixed` flag (record offset 9) so they never move.
   *
   * Positions are committed exactly once at the end and only when the run
   * is still current (`layoutGen` + `dataVersion` both unchanged); a stale
   * worker result is dropped wholesale. Afterwards positions are frozen
   * forever (needsLayout stays the only trigger); the camera either
   * restores the persisted view state from the last visit or does a
   * consistent fit.
   */
  private runLayout(): void {
    const graph = this.graph;
    const sigma = this.sigma;
    if (!graph || !sigma || graph.order === 0) return;

    const pinned = this.store.pinnedIds();
    // Pre-layout position of every node — bounded by the render cap — so
    // the commit can restore the CURRENT pin set verbatim (covers nodes
    // pinned while the worker was already running too).
    const saved = new Map<string, { x: number; y: number }>();
    graph.forEachNode((id, attrs) => {
      saved.set(id, { x: attrs['x'] as number, y: attrs['y'] as number });
    });

    const matrices = graphToByteArrays(graph, edgeWeight);
    if (pinned.size) {
      let i = 0;
      graph.forEachNode((id) => {
        if (pinned.has(id)) matrices.nodes[i + FA2_NODE_FIXED_OFFSET] = 1;
        i += FA2_NODE_STRIDE;
      });
    }

    const iterations = layoutIterations(graph.order);
    const settings: Record<string, unknown> = {
      ...FA2_DEFAULT_SETTINGS,
      ...forceAtlas2.inferSettings(graph),
      adjustSizes: true,
      strongGravityMode: true,
    };

    const gen = ++this.layoutGen;
    // The version the matrix describes — every structure change bumps it
    // (syncStructure only runs on version change), so this is the
    // generation token for "the graph still matches the serialized set".
    const runVersion = this.lastDataVersion;

    this.layoutBusy = true;
    this.layoutProgress = { done: 0, total: iterations };
    this.stageRef.nativeElement.setAttribute('aria-busy', 'true');
    recordRunStarted();
    // Store signal writes are deferred out of the sync effect — the flag
    // drives the toolbar's Cancel button, not the render path.
    queueMicrotask(() => this.store.layoutBusy.set(true));

    this.zone.runOutsideAngular(() => {
      void this.layoutRunner
        .run({
          nodes: matrices.nodes,
          edges: matrices.edges,
          settings,
          iterations,
          progressEvery: Math.max(1, Math.round(iterations / 50)),
          isStale: () =>
            gen !== this.layoutGen ||
            this.store.dataVersion().version !== runVersion,
          onProgress: (done, total) => {
            this.layoutProgress = { done, total };
          },
        })
        .then((result) => this.finishLayout(result, gen, runVersion, saved));
    });
  }

  /**
   * User-facing cancel (toolbar button): the in-flight worker is
   * terminated and nothing is committed. Positions stay exactly where
   * they were — an honest no-op, not a partial layout.
   */
  cancelLayout(): void {
    this.cancelLayoutRun();
  }

  /**
   * Invalidate + terminate the in-flight run, if any. `layoutGen` bumps
   * first so the pending resolve drops out of `finishLayout` before it
   * can touch flags owned by a newer run.
   */
  private cancelLayoutRun(): void {
    if (!this.layoutRunner.running) return;
    this.layoutGen++;
    this.layoutRunner.cancel();
    this.layoutBusy = false;
    this.layoutProgress = null;
    this.stageRef.nativeElement.removeAttribute('aria-busy');
    queueMicrotask(() => this.store.layoutBusy.set(false));
  }

  /**
   * Commit path for a finished layout pass. Runs outside Angular (worker
   * promise chain): drops stale/cancelled/error results without touching
   * the graph, then applies positions, re-validates coordinates, restores
   * pinned nodes, freezes the normalized bbox and settles the camera.
   */
  private finishLayout(
    result: Fa2RunResult,
    gen: number,
    runVersion: number,
    saved: ReadonlyMap<string, { x: number; y: number }>,
  ): void {
    // Count the terminal status even on a stale generation — the run
    // started under this component and the counter must stay honest.
    recordRunFinished(result.status);
    if (gen !== this.layoutGen) return; // superseded — a newer run owns the flags
    this.layoutBusy = false;
    this.layoutProgress = null;
    this.stageRef.nativeElement.removeAttribute('aria-busy');
    this.store.layoutBusy.set(false);

    if (result.status === 'error') {
      this.lastLayoutError =
        'O cálculo de layout falhou — as posições anteriores foram mantidas.';
      recordLayoutError(this.lastLayoutError);
      this.store.reportDiagnostics(this.lastLayoutError);
      return;
    }
    // 'cancelled' / 'stale': honest no-op — geometry untouched.
    if (result.status !== 'done' || !result.nodes) return;

    // The pass ran degraded (no usable Web Worker): positions still
    // commit, but the slower main-thread path is reported, never silent.
    if (result.fallback !== null) {
      this.store.reportDiagnostics(
        `Layout calculado na thread principal — Web Worker indisponível (${result.fallback}).`,
      );
    }
    this.lastLayoutError = null;
    recordLayoutError(null);

    const graph = this.graph;
    const sigma = this.sigma;
    if (!graph || !sigma) return;
    // The graph moved on after this run serialized it — drop wholesale.
    if (this.store.dataVersion().version !== runVersion) return;
    if (result.nodes.length !== graph.order * FA2_NODE_STRIDE) {
      this.store.reportDiagnostics(
        'Resultado de layout inconsistente com o grafo atual — descartado.',
      );
      return;
    }

    this.lastLayoutMs = result.elapsedMs;

    // Apply worker positions in serialization order — one batched
    // updateEachNodeAttributes (single graph event → single sigma pass),
    // not 2000 per-node sets. A node FA2 leaves at NaN/Infinity is dropped
    // (edges cascade) — reported, never silent.
    const matrix = result.nodes;
    const dropped: string[] = [];
    let i = 0;
    graph.updateEachNodeAttributes((id, attrs) => {
      const x = matrix[i] as number;
      const y = matrix[i + 1] as number;
      i += FA2_NODE_STRIDE;
      if (isFinitePosition(x, y)) {
        attrs['x'] = x;
        attrs['y'] = y;
      } else {
        dropped.push(id);
      }
      return attrs;
    });
    for (const id of dropped) graph.dropNode(id);
    if (dropped.length) {
      this.store.reportDiagnostics(
        `${dropped.length} nó(s) removidos após o layout por coordenadas ` +
          `inválidas: ${dropped.slice(0, 5).join(', ')}${dropped.length > 5 ? '…' : ''}`,
      );
    }

    // Pinned positions win over layout — including pins toggled mid-run.
    for (const id of this.store.pinnedIds()) {
      const pos = saved.get(id);
      if (pos && graph.hasNode(id)) {
        graph.setNodeAttribute(id, 'x', pos.x);
        graph.setNodeAttribute(id, 'y', pos.y);
      }
    }
    // No manual refresh: sigma's graph listeners already scheduled a render,
    // and reportPositions → commitData re-fires the sync effect → paintAll,
    // which owns the single full refresh per data revision.

    // Freeze normalization around the laid-out extent (+40% headroom so
    // later expansions don't shift the frame). Graph-space bbox computed
    // straight from attributes.
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    graph.forEachNode((_id, attrs) => {
      const x = attrs['x'] as number;
      const y = attrs['y'] as number;
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    });
    if (Number.isFinite(minX)) {
      const cx = (minX + maxX) / 2;
      const cy = (minY + maxY) / 2;
      const w = Math.max((maxX - minX) * 1.4, 1);
      const h = Math.max((maxY - minY) * 1.4, 1);
      sigma.setCustomBBox({ x: [cx - w / 2, cx + w / 2], y: [cy - h / 2, cy + h / 2] });
    }

    const positions = new Map<string, { x: number; y: number }>();
    graph.forEachNode((id, attrs) => {
      positions.set(id, { x: attrs['x'] as number, y: attrs['y'] as number });
    });
    // Already outside the sync effect (worker promise chain) — the write
    // to `nodes` re-runs paintAll harmlessly via the unchanged version.
    this.store.reportPositions(positions);

    // Relayout explícito invalida a câmera persistida (foi medida sobre o
    // layout anterior — restaurá-la joga todos os nós para fora do frustum).
    const isRelayout = this.store.relayoutCount() !== this.lastRelayoutCount;
    this.lastRelayoutCount = this.store.relayoutCount();
    const restored = this.viewStates.consume(this.store.viewKey());
    const cam =
      !isRelayout && restored && restored.mode === this.store.mode()
        ? restored.camera
        : null;
    if (cam && isFinitePosition(cam.x, cam.y) && Number.isFinite(cam.ratio) && cam.ratio > 0) {
      sigma.getCamera().setState({ x: cam.x, y: cam.y, ratio: cam.ratio, angle: 0 });
    } else {
      this.fitView();
    }
    if (restored) this.store.applyViewState(restored);
  }

  /**
   * Writes the live camera + selection into GraphStateService for the
   * given mode (defaults to current). Called on mode switch and destroy —
   * the only writers, so a record is always internally consistent.
   */
  private persistViewState(mode: GraphMode = this.store.mode()): void {
    const sigma = this.sigma;
    if (!sigma || this.store.repositoryId() === null) return;
    const cam = sigma.getCamera().getState();
    const camera =
      isFinitePosition(cam.x, cam.y) && Number.isFinite(cam.ratio) && cam.ratio > 0
        ? { x: cam.x, y: cam.y, ratio: cam.ratio }
        : null;
    this.viewStates.save(this.store.viewKey(mode), {
      mode,
      camera,
      selectedNodeId: this.store.selectedNodeId(),
      selectedEdgeId: this.store.selectedEdgeId(),
      pinnedIds: [...this.store.pinnedIds()],
    });
  }

  /* ------------------------------ painting ---------------------------- */

  /** Recompute display attributes (colors/sizes/labels) on the fly. */
  private paintAll(): void {
    const graph = this.graph;
    const sigma = this.sigma;
    if (!graph || !sigma) return;

    // Track reactive state inside the effect that calls this.
    const nodes = this.store.nodes();
    const edges = this.store.visibleEdges();
    const selectedNode = this.store.selectedNodeId();
    const selectedEdge = this.store.selectedEdgeId();
    const kbdFocus = this.store.kbdFocusId();
    const pinned = this.store.pinnedIds();
    const busy = this.store.expansionBusy();

    const palette = this.palette();
    const degree = new Map<string, number>();
    for (const e of edges) {
      degree.set(e.from, (degree.get(e.from) ?? 0) + 1);
      degree.set(e.to, (degree.get(e.to) ?? 0) + 1);
    }
    const incident = new Set<string>();
    if (selectedNode) {
      for (const e of edges) {
        if (e.from === selectedNode) incident.add(e.to);
        if (e.to === selectedNode) incident.add(e.from);
      }
    }

    const vmById = new Map(nodes.map((n) => [n.id, n]));
    // Batched write: updateEachNodeAttributes fires ONE graph event for the
    // whole pass — the previous per-attribute sets emitted ~7 sigma events
    // per node (a ~170 ms main-thread task at 2000 nodes on relayout).
    graph.updateEachNodeAttributes((id, attrs) => {
      const vm = vmById.get(id);
      if (!vm) return attrs;
      const isSelected = id === selectedNode;
      const isFocused = id === kbdFocus;
      const isPinned = pinned.has(id);
      const isNeighbor = incident.has(id);
      const isBusy = busy.has(id);
      attrs['label'] = vm.label;
      attrs['size'] = this.nodeSize(vm, degree.get(id) ?? 0);
      attrs['color'] = isSelected
        ? palette.nodeSelected
        : isFocused
          ? palette.nodeFocused
          : isPinned
            ? palette.nodePinned
            : vm.kind === 'file'
              ? palette.nodeFile
              : isNeighbor || !selectedNode
                ? palette.node
                : palette.edgeDim;
      attrs['zIndex'] = isSelected ? 4 : isFocused || isPinned ? 3 : 1;
      attrs['highlighted'] = isSelected || isFocused;
      attrs['forceLabel'] = isSelected || isFocused || isPinned || isBusy;
      // Selected node's neighbors get a subtle halo via zIndex bump.
      if (isNeighbor && !isSelected) attrs['zIndex'] = 2;
      return attrs;
    });

    // Index edges added since the last render before per-edge updates:
    // sigma's edge-attributes handler calls refresh({skipIndexation:true})
    // and throws on edges that were never processed.
    sigma.refresh();
    const edgeVm = new Map(edges.map((e) => [e.id, e]));
    graph.updateEachEdgeAttributes((id, attrs) => {
      const vm = edgeVm.get(id);
      // Edges removed from `visibleEdges` by a filter toggle must stop
      // rendering too — syncStructure only rebuilds the graph on a data
      // revision, so without this the canvas kept drawing edges the
      // aria-label/hiddenEdgeCount said were hidden. `hidden` zero-fills
      // the program record: undrawn AND unpickable (Cycle 8 fix).
      attrs['hidden'] = !vm;
      if (!vm) {
        attrs['label'] = '';
        return attrs;
      }
      const level = toEvidenceLevel(vm.resolutionStatus);
      const selected = id === selectedEdge;
      const touchesSel = selectedNode && (vm.from === selectedNode || vm.to === selectedNode);
      attrs['color'] = selected ? palette.nodeSelected : palette.statuses[level];
      attrs['size'] =
        (selected ? 2.5 : level === 'verified' ? 1.8 : level === 'supported' ? 1.4 : 1) *
        (touchesSel ? 1.6 : 1);
      // Non-color marker: the evidence glyph is drawn at the edge midpoint.
      attrs['label'] = EVIDENCE_MARKERS[level] ?? '';
      attrs['zIndex'] = selected || touchesSel ? 3 : 1;
      attrs['type'] = 'arrow';
      return attrs;
    });

    sigma.refresh();
  }

  private nodeSize(vm: GraphNodeVM, degree: number): number {
    if (vm.kind === 'file') {
      return Math.min(26, 8 + 2.4 * Math.sqrt(Math.max(1, vm.symbolCount ?? 1)));
    }
    return Math.min(16, 5 + 0.8 * Math.min(degree, 14));
  }

  /* ----------------------------- keyboard ----------------------------- */

  onKeydown(event: KeyboardEvent): void {
    const camera = this.sigma?.getCamera();
    const key = event.key;
    const move = (dx: number, dy: number): void => {
      if (!camera) return;
      const s = camera.getState();
      camera.setState({ x: s.x + dx * s.ratio, y: s.y + dy * s.ratio });
    };
    switch (key) {
      case 'ArrowLeft':
        move(-PAN_STEP, 0);
        break;
      case 'ArrowRight':
        move(PAN_STEP, 0);
        break;
      case 'ArrowUp':
        move(0, -PAN_STEP);
        break;
      case 'ArrowDown':
        move(0, PAN_STEP);
        break;
      case '+':
      case '=':
        this.zoomIn();
        break;
      case '-':
      case '_':
        this.zoomOut();
        break;
      case 'f':
      case 'F':
        this.fitView();
        break;
      case 'r':
      case 'R':
        this.fitView();
        break;
      case '[':
      case ']': {
        const id = this.store.cycleKbdFocus(key === ']' ? 1 : -1);
        if (id) this.focusNode(id);
        break;
      }
      case 'Enter':
      case ' ': {
        const id = this.store.kbdFocusId();
        if (id) this.store.selectNode(id);
        break;
      }
      case 'e':
      case 'E': {
        const id = this.store.selectedNodeId() ?? this.store.kbdFocusId();
        if (id) {
          const vm = this.store.nodes().find((n) => n.id === id);
          if (vm?.kind === 'file') void this.store.drillFile(vm.path, vm.symbolCount ?? 0, vm.fileId);
          else void this.store.expandNode(id);
        }
        break;
      }
      case 'p':
      case 'P': {
        const id = this.store.selectedNodeId() ?? this.store.kbdFocusId();
        if (id) this.store.togglePin(id);
        break;
      }
      case 'Escape':
        this.store.clearSelection();
        break;
      default:
        return;
    }
    event.preventDefault();
    event.stopPropagation();
  }

  /* ------------------------------ palette ----------------------------- */

  private palette(): Palette {
    const css = getComputedStyle(document.documentElement);
    const v = (name: string, fallback: string): string => {
      const val = css.getPropertyValue(name).trim();
      return val || fallback;
    };
    return {
      text: v('--atlas-color-text', '#1a1d23'),
      node: v('--atlas-color-accent', '#2855d6'),
      nodeFile: v('--atlas-evidence-supported', '#2855d6'),
      nodeSelected: v('--atlas-color-danger', '#b3261e'),
      nodePinned: v('--atlas-color-warning', '#8a5a00'),
      nodeFocused: v('--atlas-color-focus-ring', '#1b4bd6'),
      edgeDim: v('--atlas-color-border-strong', '#aeb4bf'),
      statuses: {
        verified: v('--atlas-evidence-verified', '#1e6b45'),
        supported: v('--atlas-evidence-supported', '#2855d6'),
        possible: v('--atlas-evidence-possible', '#8a5a00'),
        unresolved: v('--atlas-evidence-unresolved', '#b3261e'),
        external: v('--atlas-evidence-external', '#5a3ea8'),
        unknown: v('--atlas-color-text-muted', '#545b68'),
      },
    };
  }
}
