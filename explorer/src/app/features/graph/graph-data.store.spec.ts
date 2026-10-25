import type {
  ApiFileOccurrence,
  ApiFileOccurrences,
  ApiGraphNeighbors,
  ApiGraphNode,
  ApiGraphOverview,
  ApiService,
  ApiSymbolDetail,
} from '../../core/api/api.service';
import { ApiHttpError, ApiUnreachableError } from '../../core/api/api.errors';
import { GraphDataStore } from './graph-data.store';
import { DEFAULT_NODE_CAP, DRILL_PAGE_LIMIT } from './graph.models';

/* ---------------------------------------------------------------------
 * Mock ApiService — hand-typed against the live contract, never fakes.
 * ------------------------------------------------------------------- */

function nodeDto(id: number, qname = `m.f${id}`, path = 'm.py'): ApiGraphNode {
  return {
    id: `occ:${id}`,
    occurrence_id: id,
    symbol_id: id * 10,
    qname,
    kind: 'function',
    language: 'python',
    path,
    span: { start: id, end: id + 1 },
    parse_status: 'ok',
  };
}

function neighbors(dto: ApiGraphNeighbors): ApiGraphNeighbors {
  return dto;
}

function makeApi(overrides: Partial<Record<keyof ApiService, unknown>> = {}): ApiService {
  const base: Partial<Record<keyof ApiService, unknown>> = {
    listRepositories: jasmine
      .createSpy('listRepositories')
      .and.resolveTo({ items: [{ id: 1, identity_key: 'k', root_handle: '/repo' }], total: 1 }),
    getGraphOverview: jasmine.createSpy('getGraphOverview').and.resolveTo({
      snapshot_id: 7,
      repository_id: 1,
      items: [{ path: 'a.py', symbols: 5, language: 'python' }],
      total_files: 1,
      truncated: false,
    } satisfies ApiGraphOverview),
    getGraphNeighbors: jasmine.createSpy('getGraphNeighbors').and.resolveTo(
      neighbors({ snapshot_id: 7, edges: [], nodes: [], truncated: false }),
    ),
    getSymbolDetail: jasmine
      .createSpy('getSymbolDetail')
      .and.resolveTo({
        ...nodeDto(1),
        relations_summary: [{ type: 'calls', resolution_status: 'verified', n: 2 }],
      } satisfies ApiSymbolDetail),
    getFileOccurrences: jasmine
      .createSpy('getFileOccurrences')
      .and.resolveTo({ items: [], total: 0 } satisfies ApiFileOccurrences),
  };
  return { ...base, ...overrides } as unknown as ApiService;
}

/** One occurrence item in the GET /files/{id}/occurrences contract. */
function occurrence(id: number, path = 'm.py'): ApiFileOccurrence {
  return {
    id: `occ:${id}`,
    occurrence_id: id,
    qualified_name: `mod.fn${id}`,
    kind: 'function',
    span_start: id,
    span_end: id + 2,
    line: id,
    col: 1,
    language: 'python',
    path,
    symbol_id: id * 10,
    parse_status: 'ok',
  };
}

function occurrencesResponse(
  items: ApiFileOccurrence[],
  total = items.length,
): ApiFileOccurrences {
  return { items, total };
}

/* --------------------------------------------------------------------- */

describe('GraphDataStore — overview & phases', () => {
  it('loads the architecture overview into file nodes', async () => {
    const store = new GraphDataStore(makeApi());
    await store.loadInitial();

    expect(store.phase()).toBe('ready');
    expect(store.mode()).toBe('architecture');
    expect(store.nodes().length).toBe(1);
    expect(store.nodes()[0].id).toBe('file:a.py');
    expect(store.nodes()[0].symbolCount).toBe(5);
    expect(store.snapshotId()).toBe(7);
    expect(store.dataVersion().needsLayout).toBeTrue();
  });

  it('goes empty when no repository is indexed', async () => {
    const api = makeApi({
      listRepositories: jasmine.createSpy().and.resolveTo({ items: [], total: 0 }),
    });
    const store = new GraphDataStore(api);
    await store.loadInitial();
    expect(store.phase()).toBe('empty');
  });

  it('maps network failure to offline phase', async () => {
    const api = makeApi({
      listRepositories: jasmine.createSpy().and.rejectWith(new ApiUnreachableError(new Error('x'))),
    });
    const store = new GraphDataStore(api);
    await store.loadInitial();
    expect(store.phase()).toBe('offline');
  });
});

describe('GraphDataStore — node cap & dedup', () => {
  function overviewWith(n: number): ApiGraphOverview {
    return {
      snapshot_id: 7,
      repository_id: 1,
      items: Array.from({ length: n }, (_, i) => ({
        path: `f${i}.py`,
        symbols: i + 1,
        language: 'python',
      })),
      total_files: n,
      truncated: false,
    };
  }

  it('enforces the node cap and reports hidden count', async () => {
    const store = new GraphDataStore(
      makeApi({
        getGraphOverview: jasmine.createSpy().and.resolveTo(overviewWith(20)),
      }),
    );
    store.setNodeCap(10);
    await store.loadInitial();

    expect(store.nodes().length).toBe(10);
    expect(store.hiddenNodeCount()).toBe(10);
  });

  it('restores overflow nodes verbatim when the cap grows', async () => {
    const store = new GraphDataStore(
      makeApi({
        getGraphOverview: jasmine.createSpy().and.resolveTo(overviewWith(20)),
      }),
    );
    store.setNodeCap(10);
    await store.loadInitial();
    const hidden = store.hiddenNodeCount();
    expect(hidden).toBe(10);

    store.setNodeCap(25);
    expect(store.nodes().length).toBe(20);
    expect(store.hiddenNodeCount()).toBe(0);
    // cap shrink/grow never marks data as needing layout
    expect(store.dataVersion().needsLayout).toBeFalse();
  });

  describe('scale: cap enforcement and truncation banner at 100/500/2000 inputs', () => {
    // lf-0093 phase 16: proves the store's cap/truncation contract at the
    // sizes called out by the spec. 10k/25k are NOT RUN — no fixture of
    // that size exists and generating one client-side would not measure
    // anything the unit test doesn't already cover (the cap logic is O(n)
    // over whatever `nodesMap` holds, independent of n once n > cap).

    it('100 input nodes, default cap (500): nothing hidden, no truncation', async () => {
      const store = new GraphDataStore(
        makeApi({ getGraphOverview: jasmine.createSpy().and.resolveTo(overviewWith(100)) }),
      );
      await store.loadInitial();

      expect(store.nodes().length).toBe(100);
      expect(store.hiddenNodeCount()).toBe(0);
      expect(store.totalKnownNodes()).toBe(100);
    });

    it('500 input nodes, default cap (500): exactly at cap, nothing hidden', async () => {
      const store = new GraphDataStore(
        makeApi({ getGraphOverview: jasmine.createSpy().and.resolveTo(overviewWith(500)) }),
      );
      await store.loadInitial();

      expect(store.nodes().length).toBe(500);
      expect(store.hiddenNodeCount()).toBe(0);
    });

    it('2000 input nodes, default cap (500): truncated with accurate hidden count', async () => {
      const store = new GraphDataStore(
        makeApi({ getGraphOverview: jasmine.createSpy().and.resolveTo(overviewWith(2000)) }),
      );
      await store.loadInitial();

      expect(store.nodes().length).toBe(500);
      expect(store.hiddenNodeCount()).toBe(1500);
      expect(store.totalKnownNodes()).toBe(2000);
      // this is exactly the condition the template checks to render the
      // "Visualização limitada" banner (graph.component.html).
      expect(store.hiddenNodeCount() > 0).toBeTrue();
    });

    it('2000 input nodes with cap raised to 2000: all render, no truncation', async () => {
      const store = new GraphDataStore(
        makeApi({ getGraphOverview: jasmine.createSpy().and.resolveTo(overviewWith(2000)) }),
      );
      store.setNodeCap(2000);
      await store.loadInitial();

      expect(store.nodes().length).toBe(2000);
      expect(store.hiddenNodeCount()).toBe(0);
    });
  });

  it('dedups nodes and edges by stable id on neighbor merges', async () => {
    const api = makeApi({
      getGraphOverview: jasmine.createSpy().and.resolveTo({
        snapshot_id: 7,
        repository_id: 1,
        items: [],
        total_files: 0,
        truncated: false,
      }),
      getSymbolDetail: jasmine.createSpy().and.resolveTo({ ...nodeDto(1), relations_summary: [] }),
      getGraphNeighbors: jasmine.createSpy().and.resolveTo(
        neighbors({
          snapshot_id: 7,
          nodes: [nodeDto(1), nodeDto(2), nodeDto(3)],
          edges: [
            { id: 11, from_id: 1, to_id: 2, type: 'calls', resolution_status: 'verified', resolver: 'r' },
            { id: 11, from_id: 1, to_id: 2, type: 'calls', resolution_status: 'verified', resolver: 'r' },
          ],
          truncated: false,
        }),
      ),
    });
    const store = new GraphDataStore(api);
    await store.startAtSymbol('1');

    // seed + 2 neighbors, deduped; edge id 11 once
    expect(store.nodes().length).toBe(3);
    expect(store.edges().length).toBe(1);
    expect(store.edges()[0].id).toBe('edge:11');

    // expanding the same node again merges nothing new
    const before = store.dataVersion().version;
    await store.expandNode('occ:1');
    expect(store.nodes().length).toBe(3);
    expect(store.edges().length).toBe(1);
    expect(store.dataVersion().version).toBe(before + 1); // merge bumps version…
    expect(store.dataVersion().needsLayout).toBeFalse(); // …but never re-layouts
  });
});

describe('GraphDataStore — layout freeze flag', () => {
  async function loaded(): Promise<GraphDataStore> {
    const api = makeApi({
      getGraphNeighbors: jasmine.createSpy().and.resolveTo(
        neighbors({
          snapshot_id: 7,
          nodes: [nodeDto(10), nodeDto(11)],
          edges: [
            { id: 1, from_id: 10, to_id: 11, type: 'calls', resolution_status: 'supported', resolver: 'r' },
          ],
          truncated: true,
        }),
      ),
      getFileOccurrences: jasmine
        .createSpy('getFileOccurrences')
        .and.resolveTo(occurrencesResponse([occurrence(10), occurrence(11)])),
    });
    const store = new GraphDataStore(api);
    await store.loadInitial();
    return store;
  }

  it('flags layout once per data version, then freezes', async () => {
    const store = await loaded();
    expect(store.dataVersion().needsLayout).toBeTrue();

    // drill-down is a new context — one layout pass is expected
    await store.drillFile('m.py', 5);
    const afterDrill = store.dataVersion();
    expect(afterDrill.needsLayout).toBeTrue(); // fresh context = new layout

    const v = store.dataVersion().version;
    store.selectNode(store.nodes()[0].id);
    expect(store.dataVersion().version).toBe(v); // selection is not a data change
  });

  it('keeps selection orthogonal to layout/version', async () => {
    const store = await loaded();
    const v = store.dataVersion().version;
    const first = store.nodes()[0];
    store.selectNode(first.id);
    store.togglePin(first.id);
    store.selectEdge(null);
    store.clearSelection();
    expect(store.dataVersion().version).toBe(v);
  });

  it('only requestRelayout() re-arms the layout flag', async () => {
    const store = await loaded();
    const v = store.dataVersion().version;
    store.requestRelayout();
    expect(store.dataVersion().version).toBe(v + 1);
    expect(store.dataVersion().needsLayout).toBeTrue();
  });
});

describe('GraphDataStore — drill-down', () => {
  function drillApi(occurrences: ApiFileOccurrences = occurrencesResponse([occurrence(10), occurrence(11)])): ApiService {
    return makeApi({
      getFileOccurrences: jasmine.createSpy('getFileOccurrences').and.resolveTo(occurrences),
      getGraphNeighbors: jasmine.createSpy('getGraphNeighbors').and.callFake((occId: number) => {
        if (occId === 10) {
          return Promise.resolve(
            neighbors({
              snapshot_id: 7,
              nodes: [nodeDto(10, 'mod.fn10', 'm.py'), nodeDto(11, 'mod.fn11', 'm.py')],
              edges: [
                { id: 1, from_id: 10, to_id: 11, type: 'calls', resolution_status: 'verified', resolver: 'r' },
              ],
              truncated: false,
            }),
          );
        }
        return Promise.resolve(neighbors({ snapshot_id: 7, nodes: [], edges: [], truncated: false }));
      }),
    });
  }

  it('drills a file via the occurrences endpoint — never name search', async () => {
    const api = drillApi();
    const store = new GraphDataStore(api);
    await store.loadInitial();
    await store.drillFile('m.py', 2, 42);

    expect(api.getFileOccurrences).toHaveBeenCalledWith(
      42,
      jasmine.objectContaining({ limit: DRILL_PAGE_LIMIT }),
      jasmine.anything(),
    );
    expect(store.mode()).toBe('symbols');
    expect(store.breadcrumb().length).toBe(2);
    expect(store.breadcrumb()[1].path).toBe('m.py');
    expect(store.nodes().map((n) => n.id).sort()).toEqual(['occ:10', 'occ:11']);
    expect(store.edges().length).toBe(1);
    const ctx = store.fileContext();
    expect(ctx?.expectedSymbols).toBe(2);
    expect(ctx?.occurrencesTotal).toBe(2);
    // node carries the real index fields the endpoint returned
    const fn10 = store.nodes().find((n) => n.id === 'occ:10');
    expect(fn10?.line).toBe(10);
    expect(fn10?.span).toEqual({ start: 10, end: 12 });
    expect(fn10?.symbolId).toBe(100);
  });

  it('falls back to the path ref (with path param) when file_id is absent', async () => {
    const api = drillApi();
    const store = new GraphDataStore(api);
    await store.loadInitial();
    await store.drillFile('m.py', 2); // no fileId — overview item lacked it

    expect(api.getFileOccurrences).toHaveBeenCalledWith(
      'm.py',
      jasmine.objectContaining({ path: 'm.py' }),
      jasmine.anything(),
    );
    expect(store.phase()).toBe('ready');
  });

  it('paginates occurrences until total is covered', async () => {
    const page1 = Array.from({ length: 3 }, (_, i) => occurrence(i + 1));
    const page2 = [occurrence(4), occurrence(5)];
    const api = makeApi({
      getFileOccurrences: jasmine
        .createSpy('getFileOccurrences')
        .and.callFake((_ref: unknown, scope?: { offset?: number }) =>
          Promise.resolve(
            (scope?.offset ?? 0) === 0
              ? occurrencesResponse(page1, 5)
              : occurrencesResponse(page2, 5),
          ),
        ),
    });
    const store = new GraphDataStore(api);
    await store.loadInitial();
    await store.drillFile('m.py', 5, 7);

    expect((api.getFileOccurrences as jasmine.Spy).calls.count()).toBe(2);
    expect(store.fileContext()?.occurrencesTotal).toBe(5);
    expect(store.nodes().length).toBe(5);
  });

  it('maps a 404 from the occurrences endpoint to an honest empty state', async () => {
    const api = makeApi({
      getFileOccurrences: jasmine
        .createSpy('getFileOccurrences')
        .and.rejectWith(new ApiHttpError(404, 'Not Found', { error: { code: 'not-found' } })),
      searchCode: jasmine.createSpy('searchCode'), // must never be consulted
    });
    const store = new GraphDataStore(api);
    await store.loadInitial();
    await store.drillFile('ghost.py', 3);

    expect(store.phase()).toBe('empty');
    expect(store.mode()).toBe('symbols');
    expect(store.nodes().length).toBe(0);
    expect(store.emptyMessage()).toContain('/files/{id}/occurrences');
    // no name-search heuristic survives — searchCode is never called
    expect((api as unknown as { searchCode: jasmine.Spy }).searchCode).not.toHaveBeenCalled();
  });

  it('keeps non-404 failures on the error phase', async () => {
    const api = makeApi({
      getFileOccurrences: jasmine
        .createSpy('getFileOccurrences')
        .and.rejectWith(new ApiHttpError(500, 'Internal', null)),
    });
    const store = new GraphDataStore(api);
    await store.loadInitial();
    await store.drillFile('boom.py', 3);
    expect(store.phase()).toBe('error');
  });

  it('exits drill restoring the architecture view verbatim', async () => {
    const store = new GraphDataStore(drillApi());
    await store.loadInitial();
    const archNodes = store.nodes().map((n) => n.id);
    const archVersion = store.dataVersion().version;

    await store.drillFile('m.py', 2);
    expect(store.mode()).toBe('symbols');

    store.exitDrill();
    expect(store.mode()).toBe('architecture');
    expect(store.nodes().map((n) => n.id)).toEqual(archNodes);
    expect(store.breadcrumb().length).toBe(1);
    expect(store.fileContext()).toBeNull();
    // restore is not a layout event — positions carried on the VMs
    expect(store.dataVersion().version).toBe(archVersion + 2);
    expect(store.dataVersion().needsLayout).toBeFalse();
  });

  it('reports empty when the file has no indexed occurrences', async () => {
    const store = new GraphDataStore(drillApi(occurrencesResponse([], 0)));
    await store.loadInitial();
    await store.drillFile('ghost.py', 3, 9);
    expect(store.phase()).toBe('empty');
    expect(store.mode()).toBe('symbols');
    expect(store.nodes().length).toBe(0);
    expect(store.fileContext()?.occurrencesTotal).toBe(0);
  });
});

describe('GraphDataStore — edge filtering & expansion', () => {
  it('filters edges by type and resolution status, drops orphan edges', async () => {
    const api = makeApi({
      getSymbolDetail: jasmine.createSpy().and.resolveTo({ ...nodeDto(1), relations_summary: [] }),
      getGraphNeighbors: jasmine.createSpy().and.resolveTo(
        neighbors({
          snapshot_id: 7,
          nodes: [nodeDto(1), nodeDto(2), nodeDto(3)],
          edges: [
            { id: 1, from_id: 1, to_id: 2, type: 'calls', resolution_status: 'verified', resolver: 'r' },
            { id: 2, from_id: 2, to_id: 3, type: 'imports', resolution_status: 'unresolved', resolver: 'r' },
          ],
          truncated: false,
        }),
      ),
    });
    const store = new GraphDataStore(api);
    await store.startAtSymbol('1');
    expect(store.visibleEdges().length).toBe(2);

    store.toggleEdgeType('calls');
    expect(store.visibleEdges().map((e) => e.type)).toEqual(['calls']);

    store.clearFilters();
    store.toggleStatus('unresolved');
    expect(store.visibleEdges().map((e) => e.resolutionStatus)).toEqual(['unresolved']);
    expect(store.availableEdgeTypes()).toEqual(['calls', 'imports']);
    expect(store.availableStatuses()).toEqual(['unresolved', 'verified'].sort());
  });

  it('expandNode fetches neighbors and merges without duplicates', async () => {
    let call = 0;
    const api = makeApi({
      getSymbolDetail: jasmine.createSpy().and.resolveTo({ ...nodeDto(1), relations_summary: [] }),
      getGraphNeighbors: jasmine.createSpy().and.callFake(() => {
        call++;
        return Promise.resolve(
          call === 1
            ? neighbors({
                snapshot_id: 7,
                nodes: [nodeDto(1), nodeDto(2)],
                edges: [
                  { id: 1, from_id: 1, to_id: 2, type: 'calls', resolution_status: 'verified', resolver: 'r' },
                ],
                truncated: false,
              })
            : neighbors({
                snapshot_id: 7,
                nodes: [nodeDto(2), nodeDto(3)],
                edges: [
                  { id: 1, from_id: 1, to_id: 2, type: 'calls', resolution_status: 'verified', resolver: 'r' },
                  { id: 2, from_id: 2, to_id: 3, type: 'references', resolution_status: 'possible', resolver: 'r' },
                ],
                truncated: true,
              }),
        );
      }),
    });
    const store = new GraphDataStore(api);
    await store.startAtSymbol('1');

    await store.expandNode('occ:2');
    const ids = store.nodes().map((n) => n.id).sort();
    expect(ids).toEqual(['occ:1', 'occ:2', 'occ:3']);
    expect(store.edges().length).toBe(2);
    expect(store.neighborsTruncated()).toBeTrue();
    const node = store.nodes().find((n) => n.id === 'occ:2');
    expect(node?.expanded).toBeTrue();
    expect(node?.x).not.toBeNull(); // deterministic spiral placement
  });
});

/* ----------------------------------------------------------------------
 * Cycle-8 hardening — concurrent ops against an in-flight async op.
 * The store's contract is generation discipline: a load/drill bumps
 * `gen` and invalidates everything older; relayout/cap/filter/selection
 * do NOT, so an expansion in flight may still commit on top of them.
 * These specs pin that discipline — a mixed-state commit is a fail.
 * -------------------------------------------------------------------- */
describe('GraphDataStore — concurrent ops (cycle-8 hardening)', () => {
  /** Seed: symbol view with occ:1 + occ:2. Only the neighbors fetch for
   *  occurrence 2 — the expansion under test — is held by the gate;
   *  every other call (drill seeds, etc.) resolves immediately. */
  function gatedExpandApi(gate: Promise<ApiGraphNeighbors>, seed?: ApiGraphNeighbors): ApiService {
    return makeApi({
      getSymbolDetail: jasmine
        .createSpy()
        .and.resolveTo({ ...nodeDto(1), relations_summary: [] }),
      getGraphNeighbors: jasmine.createSpy().and.callFake((occId: number) => {
        if (occId === 2) return gate;
        if (occId === 1) {
          return Promise.resolve(
            seed ??
              neighbors({
                snapshot_id: 7,
                nodes: [nodeDto(1), nodeDto(2)],
                edges: [
                  { id: 1, from_id: 1, to_id: 2, type: 'calls', resolution_status: 'verified', resolver: 'r' },
                ],
                truncated: false,
              }),
          );
        }
        return Promise.resolve(
          neighbors({ snapshot_id: 7, nodes: [], edges: [], truncated: false }),
        );
      }),
    });
  }

  const expansionPayload = neighbors({
    snapshot_id: 7,
    nodes: [nodeDto(3)],
    edges: [
      { id: 2, from_id: 2, to_id: 3, type: 'references', resolution_status: 'possible', resolver: 'r' },
    ],
    truncated: false,
  });

  it('expand + requestRelayout mid-flight: merge commits, newest revision wins honestly', async () => {
    let release!: (v: ApiGraphNeighbors) => void;
    const gate = new Promise<ApiGraphNeighbors>((r) => (release = r));
    const store = new GraphDataStore(gatedExpandApi(gate));
    await store.startAtSymbol('1');
    const v0 = store.dataVersion().version;

    const expanding = store.expandNode('occ:2');
    expect(store.expansionBusy().has('occ:2')).toBeTrue();

    // Relayout lands while the neighbors fetch is in flight — it bumps
    // the data version (needsLayout) but must NOT kill the expansion.
    store.requestRelayout();
    expect(store.dataVersion().needsLayout).toBeTrue();
    expect(store.expansionBusy().has('occ:2')).toBeTrue();

    release(expansionPayload);
    await expanding;

    // The merge committed on top of the relayout revision — the merged
    // nodes carry deterministic positions, so the resulting revision
    // honestly does NOT need a layout (needsLayout is per-revision).
    expect(store.nodes().map((n) => n.id)).toContain('occ:3');
    expect(store.nodes().find((n) => n.id === 'occ:3')?.x).not.toBeNull();
    expect(store.dataVersion().version).toBe(v0 + 2);
    expect(store.dataVersion().needsLayout).toBeFalse();
    expect(store.expansionBusy().size).toBe(0);
  });

  it('expand + loadOverview mid-flight: the stale merge is dropped wholesale', async () => {
    let release!: (v: ApiGraphNeighbors) => void;
    const gate = new Promise<ApiGraphNeighbors>((r) => (release = r));
    const store = new GraphDataStore(gatedExpandApi(gate));
    await store.startAtSymbol('1');

    const expanding = store.expandNode('occ:2');
    // A full reload supersedes EVERYTHING in flight (gen bump + reset).
    const loading = store.loadOverview();
    release(expansionPayload);
    await Promise.all([expanding, loading]);

    // The overview replaced the node set; occ:3 from the stale response
    // must not appear — a mixed commit would leak it in.
    expect(store.mode()).toBe('architecture');
    expect(store.nodes().map((n) => n.id)).toEqual(['file:a.py']);
    expect(store.nodes().some((n) => n.id === 'occ:3')).toBeFalse();
    expect(store.expansionBusy().size).toBe(0);
    expect(store.transientError()).toBe('');
  });

  it('expand + drillFile mid-flight: the drill wins, expansion is dropped', async () => {
    let release!: (v: ApiGraphNeighbors) => void;
    const gate = new Promise<ApiGraphNeighbors>((r) => (release = r));
    const api = gatedExpandApi(gate);
    api.getFileOccurrences = jasmine
      .createSpy('getFileOccurrences')
      .and.resolveTo(occurrencesResponse([occurrence(10)]));
    const store = new GraphDataStore(api);
    await store.startAtSymbol('1');

    const expanding = store.expandNode('occ:2');
    const drilling = store.drillFile('m.py', 1, 42);
    release(expansionPayload);
    await Promise.all([expanding, drilling]);

    expect(store.mode()).toBe('symbols');
    expect(store.nodes().map((n) => n.id)).toEqual(['occ:10']);
    expect(store.nodes().some((n) => n.id === 'occ:3')).toBeFalse();
    expect(store.expansionBusy().size).toBe(0);
  });

  it('cap shrink + filter toggle mid-expand: merge still lands, filters apply', async () => {
    let release!: (v: ApiGraphNeighbors) => void;
    const gate = new Promise<ApiGraphNeighbors>((r) => (release = r));
    const store = new GraphDataStore(gatedExpandApi(gate));
    await store.startAtSymbol('1');

    const expanding = store.expandNode('occ:2');
    // View-local ops never invalidate an in-flight fetch — they compose.
    store.setNodeCap(10);
    store.toggleEdgeType('references');
    release(expansionPayload);
    await expanding;

    expect(store.nodes().map((n) => n.id)).toContain('occ:3');
    // The merged 'references' edge passes the filter; the older 'calls'
    // edge is honestly filtered out, not lost.
    expect(store.edges().length).toBe(2);
    expect(store.visibleEdges().map((e) => e.type)).toEqual(['references']);
    expect(store.expansionBusy().size).toBe(0);
  });

  it('back-to-back requestRelayout bumps generation once each', async () => {
    const store = new GraphDataStore(gatedExpandApi(new Promise(() => {})));
    await store.startAtSymbol('1');
    const v0 = store.dataVersion().version;
    const r0 = store.relayoutCount();

    store.requestRelayout();
    store.requestRelayout();

    expect(store.relayoutCount()).toBe(r0 + 2);
    expect(store.dataVersion().version).toBe(v0 + 2);
    expect(store.dataVersion().needsLayout).toBeTrue();
  });

  it('a pin toggled mid-expand survives the merge (pins are read at commit)', async () => {
    let release!: (v: ApiGraphNeighbors) => void;
    const gate = new Promise<ApiGraphNeighbors>((r) => (release = r));
    const store = new GraphDataStore(gatedExpandApi(gate));
    await store.startAtSymbol('1');

    const expanding = store.expandNode('occ:2');
    store.togglePin('occ:1'); // user pins while the fetch is in flight
    release(expansionPayload);
    await expanding;

    expect(store.pinnedIds().has('occ:1')).toBeTrue();
    expect(store.nodes().find((n) => n.id === 'occ:1')?.pinned).toBeTrue();
    expect(store.nodes().map((n) => n.id)).toContain('occ:3');
  });
});
