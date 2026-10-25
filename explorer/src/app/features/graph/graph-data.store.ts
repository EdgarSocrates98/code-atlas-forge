import { Injectable, OnDestroy, computed, signal } from '@angular/core';
import {
  ApiGraphNeighbors,
  ApiSearchHit,
  ApiService,
  ApiSymbolDetail,
} from '../../core/api/api.service';
import { ApiAbortedError, ApiHttpError, ApiUnreachableError } from '../../core/api/api.errors';
import { apiErrorText } from '../../shared/errors';
import {
  DEFAULT_NODE_CAP,
  DRILL_MAX_PAGES,
  DRILL_NEIGHBOR_BUDGET,
  DRILL_SEARCH_LIMIT,
  DrillLevel,
  GraphDataVersion,
  GraphEdgeVM,
  GraphMode,
  GraphNodeVM,
  GraphPhase,
  NEIGHBOR_LIMIT,
  OVERVIEW_LIMIT,
} from './graph.models';
import {
  fileStem,
  hashPosition,
  occNodeId,
  parseOccurrenceId,
  searchHitToVM,
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
  readonly fileContext = signal<{ path: string; expectedSymbols: number } | null>(
    null,
  );

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
   * Drill into a file aggregate: seeds symbol nodes via structural search
   * (occurrence ids for the file's symbols) and merges a bounded number of
   * neighbor fetches to draw intra/inter-file edges.
   */
  async drillFile(path: string, expectedSymbols: number): Promise<void> {
    if (this.drillBusy()) return;
    const gen = ++this.gen;
    this.drillBusy.set(true);
    this.phase.set('loading');

    // Freeze the architecture view so "Voltar" restores it verbatim.
    this.archSnapshot = this.freeze();
    this.resetGraphState();
    this.mode.set('symbols');
    this.fileContext.set({ path, expectedSymbols });
    this.breadcrumb.set([
      { kind: 'architecture' },
      { kind: 'file', path, expectedSymbols },
    ]);

    try {
      const hits = await this.searchFileOccurrences(path, gen);
      if (gen !== this.gen) return;

      if (!hits.length) {
        this.commitData();
        this.bumpVersion(true);
        this.phase.set('empty');
        this.emptyMessage.set(
          `Não foi possível localizar ocorrências de símbolo em ${path} via busca estrutural.`,
        );
        return;
      }

      // Every occurrence in the file becomes a node — even ones without
      // relations — so the drilled view honestly represents the file.
      this.mergeNodes(hits.map(searchHitToVM));

      // Bounded neighbor expansion around the first seeds.
      const seeds = hits.map((h) => h.occurrence_id).slice(0, DRILL_NEIGHBOR_BUDGET);
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

  /* ============================= internals ============================ */

  /**
   * Occurrence seeds for a file via structural search. The API requires a
   * non-empty `q`, so we probe two honest queries — the file stem (module
   * qnames embed it: `module.scope.name`) and `.` (dotted qnames) — merge
   * and dedup by occurrence_id. Coverage is reported via fileContext vs.
   * the returned count; nothing is fabricated.
   */
  private async searchFileOccurrences(
    path: string,
    gen: number,
  ): Promise<ApiSearchHit[]> {
    const collected = new Map<number, ApiSearchHit>();
    const queries = [...new Set([fileStem(path), '.'].filter((q) => q.length > 0))];
    for (const q of queries) {
      let cursor: string | null = null;
      let pages = 0;
      do {
        const res = await this.api.searchCode(
          {
            q,
            strategy: 'structural',
            path,
            repository_root: this.repositoryRoot() || undefined,
            snapshot_id: this.snapshotId() ?? undefined,
            limit: DRILL_SEARCH_LIMIT,
            cursor,
          },
          { signal: this.controller.signal },
        );
        if (gen !== this.gen) return [];
        for (const hit of res.items) {
          // `path` is a substring filter server-side; keep exact-file hits.
          if (hit.path === path && hit.occurrence_id > 0) {
            collected.set(hit.occurrence_id, hit);
          }
        }
        cursor = res.next_cursor;
        pages++;
      } while (cursor && pages < DRILL_MAX_PAGES);
    }
    return [...collected.values()].sort((a, b) => a.occurrence_id - b.occurrence_id);
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
