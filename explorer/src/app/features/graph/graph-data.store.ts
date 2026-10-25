import { Injectable, OnDestroy, computed, signal } from '@angular/core';
import {
  ApiFileOccurrence,
  ApiFileOccurrences,
  ApiGraphNeighbors,
  ApiService,
  ApiSymbolDetail,
} from '../../core/api/api.service';
import { ApiAbortedError, ApiHttpError, ApiUnreachableError } from '../../core/api/api.errors';
import { apiErrorText } from '../../shared/errors';
import {
  DEFAULT_NODE_CAP,
  DRILL_MAX_PAGES,
  DRILL_NEIGHBOR_BUDGET,
  DRILL_PAGE_LIMIT,
  DrillLevel,
  FileDrillContext,
  GraphDataVersion,
  GraphEdgeVM,
  GraphMode,
  GraphNodeVM,
  GraphPhase,
  NEIGHBOR_LIMIT,
  OVERVIEW_LIMIT,
} from './graph.models';
import { GraphStateService } from './graph-state.service';
import type { GraphViewState } from './graph-state.service';
import {
  hashPosition,
  occNodeId,
  occurrenceToVM,
  parseOccurrenceId,
  spiralPosition,
  toEdgeVM,
  toFileNodeVM,
  toSymbolNodeVM,
} from './graph.utils';

interface FrozenView {
  nodes: Map<string, GraphNodeVM>;
  edges: Map<string, GraphEdgeVM>;
  hidden: Map<string, GraphNodeVM>;
  serverTruncated: boolean;
}

/**
 * Signal store for the graph screen — the single source of truth for
 * nodes/edges, caps, filters, selection, drill-down and layout flags.
 *
 * Invariants the specs pin down:
 * - node ids are stable (`occ:{id}` / `file:{path}`) — merges dedup by key;
 * - `dataVersion.needsLayout` is the ONLY signal that may trigger a layout
 *   pass; selection, filters and incremental merges never do;
 * - hidden (over-cap) nodes keep their data so raising the cap restores
 *   them verbatim;
 * - every async path is generation-guarded: stale responses are dropped.
 */
@Injectable()
export class GraphDataStore implements OnDestroy {
  /* ------------------------------- scope ---------------------------- */
  readonly repositoryId = signal<number | null>(null);
  readonly snapshotId = signal<number | null>(null);
  readonly repositoryRoot = signal('');

  /* ------------------------------- phase ---------------------------- */
  readonly phase = signal<GraphPhase>('idle');
  readonly errorMessage = signal('');
  readonly errorDetail = signal('');
  readonly emptyMessage = signal('');
  /** Non-fatal errors (e.g. a single expansion failed) — graph stays usable. */
  readonly transientError = signal('');

  /* ------------------------------- data ----------------------------- */
  private readonly nodesMap = new Map<string, GraphNodeVM>();
  private readonly overflowMap = new Map<string, GraphNodeVM>();
  private readonly edgesMap = new Map<string, GraphEdgeVM>();

  readonly nodes = signal<GraphNodeVM[]>([]);
  readonly edges = signal<GraphEdgeVM[]>([]);
  readonly hiddenNodeCount = signal(0);
  /** Server reported more rows than it returned (overview or neighbors). */
  readonly serverTruncated = signal(false);
  readonly neighborsTruncated = signal(false);
  readonly fileContext = signal<FileDrillContext | null>(null);

  readonly nodeCap = signal(DEFAULT_NODE_CAP);

  readonly mode = signal<GraphMode>('architecture');
  readonly breadcrumb = signal<DrillLevel[]>([{ kind: 'architecture' }]);
  readonly dataVersion = signal<GraphDataVersion>({ version: 0, needsLayout: false });

  /* ---------------------------- interaction -------------------------- */
  readonly selectedNodeId = signal<string | null>(null);
  readonly selectedEdgeId = signal<string | null>(null);
  readonly kbdFocusId = signal<string | null>(null);
  readonly pinnedIds = signal<ReadonlySet<string>>(new Set());
  readonly expansionBusy = signal<ReadonlySet<string>>(new Set());
  readonly drillBusy = signal(false);
  readonly detailLoading = signal(false);
  readonly selectedDetail = signal<ApiSymbolDetail | null>(null);
  readonly rendererFailed = signal(false);
  /**
   * WebGL context currently lost (GPU reset/driver). Distinct from
   * `rendererFailed`: the canvas may still recover on contextrestored.
   */
  readonly rendererLost = signal(false);

  readonly edgeTypeFilter = signal<ReadonlySet<string>>(new Set());
  readonly statusFilter = signal<ReadonlySet<string>>(new Set());

  /* ----------------------------- computed ---------------------------- */
  /* NOTE: computeds must read the `nodes()`/`edges()` signals (not the
   * private maps) so Angular can track dependencies. */

  readonly selectedNode = computed(() => {
    const id = this.selectedNodeId();
    return id ? (this.nodes().find((n) => n.id === id) ?? null) : null;
  });

  readonly selectedEdge = computed(() => {
    const id = this.selectedEdgeId();
    return id ? (this.edges().find((e) => e.id === id) ?? null) : null;
  });

  /** Edges whose endpoints are both visible and that pass the filters. */
  readonly visibleEdges = computed(() => {
    const typeF = this.edgeTypeFilter();
    const statusF = this.statusFilter();
    const nodeIds = new Set(this.nodes().map((n) => n.id));
    return this.edges().filter(
      (e) =>
        nodeIds.has(e.from) &&
        nodeIds.has(e.to) &&
        (typeF.size === 0 || typeF.has(e.type)) &&
        (statusF.size === 0 || statusF.has(e.resolutionStatus)),
    );
  });

  readonly hiddenEdgeCount = computed(() => this.edges().length - this.visibleEdges().length);

  readonly availableEdgeTypes = computed(() => {
    const s = new Set<string>();
    for (const e of this.edges()) s.add(e.type);
    return [...s].sort();
  });

  readonly availableStatuses = computed(() => {
    const s = new Set<string>();
    for (const e of this.edges()) s.add(e.resolutionStatus);
    return [...s].sort();
  });

  readonly totalKnownNodes = computed(() => this.nodes().length + this.hiddenNodeCount());

  /* --------------------------- async plumbing ------------------------ */
  private gen = 0;
  private readonly controller = new AbortController();
  private archSnapshot: FrozenView | null = null;
  private detailGen = 0;

  constructor(private readonly api: ApiService) {}

  ngOnDestroy(): void {
    this.controller.abort();
  }

  /* =============================== loads ============================= */

  /** Entry point: resolve repository, then load the architecture overview. */
  async loadInitial(): Promise<void> {
    this.phase.set('loading');
    try {
      const repos = await this.api.listRepositories({ signal: this.controller.signal });
      if (!repos.items.length) {
        this.phase.set('empty');
        this.emptyMessage.set(
          'Nenhum repositório indexado. Indexe um diretório com `code-atlas index` e recarregue.',
        );
        return;
      }
      this.repositoryId.set(repos.items[0].id);
      this.repositoryRoot.set(repos.items[0].root_handle);
    } catch (error) {
      this.mapLoadError(error);
      return;
    }
    await this.loadOverview();
  }

  /** Full reload of the file-aggregate (architecture) view. */
  async loadOverview(): Promise<void> {
    const gen = ++this.gen;
    this.phase.set('loading');
    this.resetGraphState();

    try {
      const ov = await this.api.getGraphOverview(
        {
          repositoryId: this.repositoryId() ?? undefined,
          snapshotId: this.snapshotId() ?? undefined,
          limit: OVERVIEW_LIMIT,
        },
        { signal: this.controller.signal },
      );
      if (gen !== this.gen) return;

      this.repositoryId.set(ov.repository_id);
      this.snapshotId.set(ov.snapshot_id);

      for (const item of ov.items) {
        const vm = toFileNodeVM(item);
        this.nodesMap.set(vm.id, vm);
      }
      this.serverTruncated.set(ov.truncated || ov.total_files > ov.items.length);
      this.mode.set('architecture');
      this.breadcrumb.set([{ kind: 'architecture' }]);
      this.fileContext.set(null);
      this.applyCap();
      this.bumpVersion(true);
      this.commitData();
      this.phase.set(this.nodesMap.size ? 'ready' : 'empty');
      if (!this.nodesMap.size) {
        this.emptyMessage.set('O snapshot atual não possui arquivos com símbolos.');
      }
    } catch (error) {
      if (gen !== this.gen) return;
      this.mapLoadError(error);
    }
  }

  /**
   * Drill into a file aggregate: seeds symbol nodes from
   * GET /files/{file_id}/occurrences (the real index data — no name-search
   * heuristic) and merges a bounded number of neighbor fetches to draw
   * intra/inter-file edges.
   *
   * `fileId` is `source_files.id` when the backend reports it on the
   * overview item; otherwise the path is sent as the route ref — a 404
   * answer maps to an honest empty state, never to a fabricated fallback.
   */
  async drillFile(
    path: string,
    expectedSymbols: number,
    fileId: number | null = null,
  ): Promise<void> {
    if (this.drillBusy()) return;
    const gen = ++this.gen;
    this.drillBusy.set(true);
    this.phase.set('loading');

    // Freeze the architecture view so "Voltar" restores it verbatim.
    this.archSnapshot = this.freeze();
    this.resetGraphState();
    this.mode.set('symbols');
    this.fileContext.set({ path, expectedSymbols, occurrencesTotal: null });
    this.breadcrumb.set([
      { kind: 'architecture' },
      { kind: 'file', path, expectedSymbols },
    ]);

    try {
      const result = await this.fetchFileOccurrences(path, fileId, gen);
      if (gen !== this.gen) return;

      if (result === null) {
        // Endpoint absent (404): honest empty state — the search heuristic
        // it replaces is gone for good.
        this.commitData();
        this.bumpVersion(true);
        this.phase.set('empty');
        this.emptyMessage.set(
          `O endpoint de ocorrências (GET /files/{id}/occurrences) não está ` +
            `disponível nesta versão da API — o conteúdo de ${path} não pode ` +
            `ser inspecionado no grafo.`,
        );
        return;
      }

      this.fileContext.set({
        path,
        expectedSymbols,
        occurrencesTotal: result.total,
      });

      if (!result.items.length) {
        this.commitData();
        this.bumpVersion(true);
        this.phase.set('empty');
        this.emptyMessage.set(
          `Nenhuma ocorrência de símbolo indexada para ${path} neste snapshot.`,
        );
        return;
      }

      // Every occurrence in the file becomes a node — even ones without
      // relations — so the drilled view honestly represents the file.
      this.mergeNodes(result.items.map((item) => occurrenceToVM(item, path)));

      // Bounded neighbor expansion around the first seeds.
      const seeds = result.items.map((o) => o.id).slice(0, DRILL_NEIGHBOR_BUDGET);
      let truncated = false;
      for (const seed of seeds) {
        const res = await this.api.getGraphNeighbors(
          seed,
          'both',
          {
            repositoryId: this.repositoryId() ?? undefined,
            snapshotId: this.snapshotId() ?? undefined,
            limit: NEIGHBOR_LIMIT,
          },
          { signal: this.controller.signal },
        );
        if (gen !== this.gen) return;
        this.snapshotId.set(res.snapshot_id);
        this.mergeNeighbors(res, occNodeId(seed));
        truncated ||= res.truncated;
      }
      this.neighborsTruncated.set(truncated);

      this.applyCap();
      this.commitData();
      this.bumpVersion(true);
      this.phase.set(this.nodesMap.size ? 'ready' : 'empty');
      if (!this.nodesMap.size) {
        this.emptyMessage.set('O arquivo não possui relações navegáveis no grafo.');
      }
    } catch (error) {
      if (gen !== this.gen) return;
      this.mapLoadError(error);
    } finally {
      if (gen === this.gen) this.drillBusy.set(false);
    }
  }

  /** Back to the architecture view with positions/context preserved. */
  exitDrill(): void {
    const snap = this.archSnapshot;
    if (!snap) {
      // Nothing frozen (e.g. deep-link into a symbol) — reload overview.
      void this.loadOverview();
      return;
    }
    this.resetGraphState();
    for (const [k, v] of snap.nodes) this.nodesMap.set(k, v);
    for (const [k, v] of snap.edges) this.edgesMap.set(k, v);
    for (const [k, v] of snap.hidden) this.overflowMap.set(k, v);
    this.serverTruncated.set(snap.serverTruncated);
    this.archSnapshot = null;
    this.mode.set('architecture');
    this.breadcrumb.set([{ kind: 'architecture' }]);
    this.fileContext.set(null);
    // Rebuild the pin set from the restored VMs — freeze() kept the flags
    // on the nodes, and the signal must agree or a later relayout would
    // move nodes that still render as pinned.
    this.pinnedIds.set(
      new Set([...this.nodesMap.values()].filter((n) => n.pinned).map((n) => n.id)),
    );
    this.commitData();
    this.hiddenNodeCount.set(this.overflowMap.size);
    this.bumpVersion(false); // positions already on the nodes — no re-layout
    this.phase.set(this.nodesMap.size ? 'ready' : 'empty');
  }

  /**
   * Deep-link entry: start the symbol-level graph at one occurrence
   * (query param `?focus=123` or `?focus=occ:123`).
   */
  async startAtSymbol(rawFocus: string): Promise<void> {
    const occId = parseOccurrenceId(rawFocus) ?? Number(rawFocus);
    if (!Number.isFinite(occId) || occId <= 0) {
      await this.loadInitial();
      return;
    }
    const gen = ++this.gen;
    this.phase.set('loading');
    this.resetGraphState();
    try {
      if (this.repositoryId() === null) {
        const repos = await this.api.listRepositories({ signal: this.controller.signal });
        if (gen !== this.gen) return;
        if (!repos.items.length) {
          this.phase.set('empty');
          this.emptyMessage.set('Nenhum repositório indexado.');
          return;
        }
        this.repositoryId.set(repos.items[0].id);
        this.repositoryRoot.set(repos.items[0].root_handle);
      }
      const detail = await this.api.getSymbolDetail(
        occId,
        { repositoryId: this.repositoryId() ?? undefined },
        { signal: this.controller.signal },
      );
      if (gen !== this.gen) return;
      this.mergeNodes([toSymbolNodeVM(detail)]);
      const res = await this.api.getGraphNeighbors(
        occId,
        'both',
        {
          repositoryId: this.repositoryId() ?? undefined,
          snapshotId: this.snapshotId() ?? undefined,
          limit: NEIGHBOR_LIMIT,
        },
        { signal: this.controller.signal },
      );
      if (gen !== this.gen) return;
      this.snapshotId.set(res.snapshot_id);
      this.mergeNeighbors(res, occNodeId(occId));
      this.neighborsTruncated.set(res.truncated);
      this.mode.set('symbols');
      this.fileContext.set(null);
      this.breadcrumb.set([
        { kind: 'architecture' },
        { kind: 'file', path: detail.path, expectedSymbols: 0 },
      ]);
      this.applyCap();
      this.commitData();
      this.bumpVersion(true);
      this.phase.set('ready');
      this.selectNode(occNodeId(occId));
    } catch (error) {
      if (gen !== this.gen) return;
      this.mapLoadError(error);
    }
  }

  /* ============================ interaction ========================== */

  /** Lazy expansion of a symbol node via /graph/neighbors. */
  async expandNode(nodeId: string): Promise<void> {
    const node = this.nodesMap.get(nodeId);
    if (!node || node.kind !== 'symbol' || node.occurrenceId === null) return;
    if (this.expansionBusy().has(nodeId)) return;

    const gen = this.gen;
    this.expansionBusy.update((s) => new Set(s).add(nodeId));
    try {
      const res = await this.api.getGraphNeighbors(
        node.occurrenceId,
        'both',
        {
          repositoryId: this.repositoryId() ?? undefined,
          snapshotId: this.snapshotId() ?? undefined,
          limit: NEIGHBOR_LIMIT,
        },
        { signal: this.controller.signal },
      );
      if (gen !== this.gen) return;
      this.snapshotId.set(res.snapshot_id);
      this.mergeNeighbors(res, nodeId);
      node.expanded = true;
      this.neighborsTruncated.update((t) => t || res.truncated);
      this.applyCap();
      this.commitData();
      this.bumpVersion(false); // incremental merge — never re-layout
    } catch (error) {
      if (error instanceof ApiAbortedError || gen !== this.gen) return;
      this.transientError.set(`Falha ao expandir: ${apiErrorText(error)}`);
    } finally {
      this.expansionBusy.update((s) => {
        const next = new Set(s);
        next.delete(nodeId);
        return next;
      });
    }
  }

  selectNode(nodeId: string | null): void {
    if (nodeId && !this.nodesMap.has(nodeId)) return;
    this.selectedNodeId.set(nodeId);
    if (nodeId) this.selectedEdgeId.set(null);
    void this.loadDetailFor(nodeId);
  }

  selectEdge(edgeId: string | null): void {
    if (edgeId && !this.edgesMap.has(edgeId)) return;
    this.selectedEdgeId.set(edgeId);
    if (edgeId) this.selectedNodeId.set(null);
  }

  clearSelection(): void {
    this.selectedNodeId.set(null);
    this.selectedEdgeId.set(null);
    this.selectedDetail.set(null);
  }

  togglePin(nodeId: string): void {
    const node = this.nodesMap.get(nodeId);
    if (!node) return;
    node.pinned = !node.pinned;
    this.pinnedIds.update((s) => {
      const next = new Set(s);
      if (node.pinned) next.add(nodeId);
      else next.delete(nodeId);
      return next;
    });
    this.commitData();
  }

  setKbdFocus(nodeId: string | null): void {
    this.kbdFocusId.set(nodeId);
  }

  /** Cycle keyboard focus through visible nodes; returns the new focus. */
  cycleKbdFocus(direction: 1 | -1): string | null {
    const list = this.nodes();
    if (!list.length) return null;
    const cur = this.kbdFocusId();
    const idx = cur ? list.findIndex((n) => n.id === cur) : -1;
    const next = list[(idx + direction + list.length) % list.length];
    this.kbdFocusId.set(next.id);
    return next.id;
  }

  /* ---------------------------- filtering ---------------------------- */

  toggleEdgeType(type: string): void {
    this.edgeTypeFilter.update((s) => toggleInSet(s, type));
  }

  toggleStatus(status: string): void {
    this.statusFilter.update((s) => toggleInSet(s, status));
  }

  clearFilters(): void {
    this.edgeTypeFilter.set(new Set());
    this.statusFilter.set(new Set());
  }

  setNodeCap(value: number): void {
    const cap = Math.max(10, Math.min(2000, Math.floor(value) || DEFAULT_NODE_CAP));
    this.nodeCap.set(cap);
    this.applyCap();
    this.commitData();
    // positions already on nodes — cap changes never trigger layout
    this.bumpVersion(false);
  }

  /** User-triggered re-layout: the ONLY way to recompute positions. */
  requestRelayout(): void {
    this.bumpVersion(true);
  }

  /* ------------------------- canvas feedback ------------------------- */

  /** Canvas reports computed positions back so restores keep them. */
  reportPositions(positions: ReadonlyMap<string, { x: number; y: number }>): void {
    for (const [id, p] of positions) {
      const node = this.nodesMap.get(id) ?? this.overflowMap.get(id);
      if (node) {
        node.x = p.x;
        node.y = p.y;
      }
    }
    this.commitData();
  }

  markRendererFailed(): void {
    this.rendererFailed.set(true);
  }

  /** WebGL context lost/restored — reported by the canvas listeners. */
  markRendererLost(lost: boolean): void {
    this.rendererLost.set(lost);
  }

  /**
   * Non-fatal canvas diagnostic (e.g. nodes dropped for invalid
   * coordinates) — surfaced as a dismissible warning, never silent.
   */
  reportDiagnostics(message: string): void {
    console.warn(`[graph] ${message}`);
    this.transientError.set(message);
  }

  /** Persistence key for the current view (repo + snapshot + mode). */
  viewKey(mode: GraphMode = this.mode()): string {
    return GraphStateService.key(this.repositoryId(), this.snapshotId(), mode);
  }

  /**
   * Re-applies a persisted selection/pins after a reload. Every lookup is
   * guarded against the current data — stale ids simply do not restore.
   */
  applyViewState(state: GraphViewState): void {
    if (state.pinnedIds.length) {
      const kept = new Set<string>();
      for (const id of state.pinnedIds) {
        const node = this.nodesMap.get(id);
        if (node) {
          node.pinned = true;
          kept.add(id);
        }
      }
      if (kept.size) {
        this.pinnedIds.set(kept);
        this.commitData();
      }
    }
    if (state.selectedNodeId && this.nodesMap.has(state.selectedNodeId)) {
      this.selectNode(state.selectedNodeId);
    } else if (state.selectedEdgeId && this.edgesMap.has(state.selectedEdgeId)) {
      this.selectEdge(state.selectedEdgeId);
    }
  }

  /* ============================= internals ============================ */

  /**
   * Paginated fetch of GET /files/{file_id}/occurrences (contract:
   * `{items, total}`). Returns `null` on 404 — the endpoint or the file is
   * absent and the caller renders the honest empty state. Any other error
   * propagates to the normal error mapping.
   */
  private async fetchFileOccurrences(
    path: string,
    fileId: number | null,
    gen: number,
  ): Promise<{ items: ApiFileOccurrence[]; total: number } | null> {
    const ref = fileId ?? path;
    const items: ApiFileOccurrence[] = [];
    let total = 0;
    let pages = 0;
    try {
      do {
        const res: ApiFileOccurrences = await this.api.getFileOccurrences(
          ref,
          {
            repositoryId: this.repositoryId() ?? undefined,
            snapshotId: this.snapshotId() ?? undefined,
            limit: DRILL_PAGE_LIMIT,
            offset: items.length,
            // path only helps a path-aware backend when the id is unknown.
            path: fileId === null ? path : undefined,
          },
          { signal: this.controller.signal },
        );
        if (gen !== this.gen) return { items: [], total: 0 };
        total = res.total ?? 0;
        items.push(...(res.items ?? []));
        pages++;
        if (!(res.items?.length)) break;
      } while (items.length < total && pages < DRILL_MAX_PAGES);
    } catch (error) {
      if (error instanceof ApiHttpError && error.status === 404) return null;
      throw error;
    }
    return { items, total };
  }

  private mergeNeighbors(res: ApiGraphNeighbors, seedNodeId: string): void {
    const seed = this.nodesMap.get(seedNodeId) ?? this.overflowMap.get(seedNodeId);
    const newNodes: GraphNodeVM[] = [];
    for (const dto of res.nodes) {
      const id = occNodeId(dto.occurrence_id);
      if (this.nodesMap.has(id) || this.overflowMap.has(id)) {
        this.upgradeNode(this.nodesMap.get(id) ?? this.overflowMap.get(id)!, dto);
        continue;
      }
      const vm = toSymbolNodeVM(dto);
      newNodes.push(vm);
      this.nodesMap.set(id, vm);
    }
    // Deterministic spiral placement around the seed for fresh nodes.
    const seedPos =
      seed && seed.x !== null && seed.y !== null
        ? { x: seed.x, y: seed.y }
        : { x: 0, y: 0 };
    newNodes
      .sort((a, b) => (a.occurrenceId ?? 0) - (b.occurrenceId ?? 0))
      .forEach((n, i) => {
        const p = spiralPosition(seedPos, i);
        n.x = p.x;
        n.y = p.y;
      });
    for (const e of res.edges) {
      const vm = toEdgeVM(e);
      if (!this.edgesMap.has(vm.id)) this.edgesMap.set(vm.id, vm);
    }
  }

  private mergeNodes(nodes: GraphNodeVM[]): void {
    const total = nodes.length;
    nodes.forEach((n, i) => {
      if (!this.nodesMap.has(n.id)) {
        if (n.x === null || n.y === null) {
          const p = hashPosition(n.id, i, total);
          n.x = p.x;
          n.y = p.y;
        }
        this.nodesMap.set(n.id, n);
      }
    });
  }

  /** Fill missing fields on a VM when a richer DTO arrives later. */
  private upgradeNode(vm: GraphNodeVM, dto: ApiGraphNeighbors['nodes'][number]): void {
    if (vm.symbolId === null) vm.symbolId = dto.symbol_id;
    if (!vm.parseStatus) vm.parseStatus = dto.parse_status;
    if (!vm.span && dto.span) vm.span = { start: dto.span.start, end: dto.span.end };
    if (!vm.qname) vm.qname = dto.qname;
    if (!vm.symbolKind) vm.symbolKind = dto.kind;
    if (!vm.language) vm.language = dto.language;
  }

  private applyCap(): void {
    const cap = this.nodeCap();
    if (this.nodesMap.size <= cap) {
      // restore overflow when there's room again
      for (const [id, node] of [...this.overflowMap]) {
        if (this.nodesMap.size >= cap) break;
        this.overflowMap.delete(id);
        this.nodesMap.set(id, node);
      }
    }
    if (this.nodesMap.size > cap) {
      const priority = new Set<string>([
        ...this.pinnedIds(),
        ...(this.selectedNodeId() ? [this.selectedNodeId()!] : []),
      ]);
      for (const [id, n] of this.nodesMap) {
        if (n.expanded) priority.add(id);
      }
      const keep = new Map<string, GraphNodeVM>();
      // priority nodes first (insertion order), then fill
      for (const [id, n] of this.nodesMap) {
        if (priority.has(id) && keep.size < cap) keep.set(id, n);
      }
      for (const [id, n] of this.nodesMap) {
        if (keep.size >= cap) break;
        if (!keep.has(id)) keep.set(id, n);
      }
      for (const [id, n] of this.nodesMap) {
        if (!keep.has(id)) this.overflowMap.set(id, n);
      }
      this.nodesMap.clear();
      for (const [id, n] of keep) this.nodesMap.set(id, n);
    }
    this.hiddenNodeCount.set(this.overflowMap.size);
  }

  private freeze(): FrozenView {
    return {
      nodes: new Map(this.nodesMap),
      edges: new Map(this.edgesMap),
      hidden: new Map(this.overflowMap),
      serverTruncated: this.serverTruncated(),
    };
  }

  private resetGraphState(): void {
    this.nodesMap.clear();
    this.edgesMap.clear();
    this.overflowMap.clear();
    this.hiddenNodeCount.set(0);
    this.serverTruncated.set(false);
    this.neighborsTruncated.set(false);
    this.selectedNodeId.set(null);
    this.selectedEdgeId.set(null);
    this.selectedDetail.set(null);
    this.kbdFocusId.set(null);
    this.pinnedIds.set(new Set());
    this.expansionBusy.set(new Set());
    this.clearFilters();
    this.commitData();
  }

  private commitData(): void {
    this.nodes.set([...this.nodesMap.values()]);
    this.edges.set([...this.edgesMap.values()]);
    this.hiddenNodeCount.set(this.overflowMap.size);
  }

  private bumpVersion(needsLayout: boolean): void {
    this.dataVersion.update((v) => ({ version: v.version + 1, needsLayout }));
  }

  private mapLoadError(error: unknown): void {
    if (error instanceof ApiAbortedError) return;
    if (error instanceof ApiUnreachableError) {
      this.phase.set('offline');
      this.errorMessage.set('');
      this.errorDetail.set(apiErrorText(error));
      return;
    }
    this.phase.set('error');
    this.errorMessage.set(apiErrorText(error));
    if (error instanceof ApiHttpError) {
      const body = error.body as { error?: { code?: string } } | null;
      const code = body?.error?.code ?? '';
      this.errorDetail.set(code ? `Código: ${code}` : `${error.status} ${error.statusText}`);
    } else {
      this.errorDetail.set('');
    }
  }

  private async loadDetailFor(nodeId: string | null): Promise<void> {
    const occId = nodeId ? parseOccurrenceId(nodeId) : null;
    if (occId === null) {
      this.selectedDetail.set(null);
      return;
    }
    const myGen = ++this.detailGen;
    this.detailLoading.set(true);
    this.selectedDetail.set(null);
    try {
      const detail = await this.api.getSymbolDetail(
        occId,
        {
          repositoryId: this.repositoryId() ?? undefined,
          snapshotId: this.snapshotId() ?? undefined,
        },
        { signal: this.controller.signal },
      );
      if (myGen !== this.detailGen) return;
      this.selectedDetail.set(detail);
    } catch (error) {
      if (myGen !== this.detailGen) return;
      this.selectedDetail.set(null);
      if (error instanceof ApiAbortedError) return;
      // Detail failure is non-fatal: the node data stays inspectable.
    } finally {
      if (myGen === this.detailGen) this.detailLoading.set(false);
    }
  }
}

function toggleInSet<T>(set: ReadonlySet<T>, value: T): Set<T> {
  const next = new Set(set);
  if (next.has(value)) next.delete(value);
  else next.add(value);
  return next;
}
