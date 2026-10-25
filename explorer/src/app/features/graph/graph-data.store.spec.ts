import type {
  ApiGraphNeighbors,
  ApiGraphNode,
  ApiGraphOverview,
  ApiSearchHit,
  ApiSearchResponse,
  ApiService,
  ApiSymbolDetail,
} from '../../core/api/api.service';
import { ApiUnreachableError } from '../../core/api/api.errors';
import { GraphDataStore } from './graph-data.store';
import { DEFAULT_NODE_CAP } from './graph.models';

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
    searchCode: jasmine.createSpy('searchCode').and.resolveTo({
      schema_version: 1,
      repository: '/repo',
      snapshot_id: 7,
      query: 'x',
      strategy: 'structural',
      ranking_version: 'v',
      items: [],
      limit: 200,
      truncated: false,
      next_cursor: null,
      diagnostics: [],
    } satisfies ApiSearchResponse),
  };
  return { ...base, ...overrides } as unknown as ApiService;
}

function searchHit(id: number, path: string): ApiSearchHit {
  return {
    occurrence_id: id,
    qualified_name: `mod.fn${id}`,
    kind: 'function',
    language: 'python',
    path,
    span: { start: id, end: id + 2 },
    score: 1,
    strategy: 'structural',
    matched_fields: ['qualified_name'],
    evidence: [`occ:${id}`],
  };
}

function searchResponse(items: ApiSearchHit[]): ApiSearchResponse {
  return {
    schema_version: 1,
    repository: '/repo',
    snapshot_id: 7,
    query: 'q',
    strategy: 'structural',
    ranking_version: 'v',
    items,
    limit: 200,
    truncated: false,
    next_cursor: null,
    diagnostics: [],
  };
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
      searchCode: jasmine.createSpy().and.resolveTo(searchResponse([searchHit(10, 'm.py'), searchHit(11, 'm.py')])),
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
  function drillApi(): ApiService {
    return makeApi({
      searchCode: jasmine.createSpy('searchCode').and.resolveTo(
        searchResponse([searchHit(10, 'm.py'), searchHit(11, 'm.py'), searchHit(12, 'other.py')]),
      ),
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

  it('drills a file into its symbol occurrences (exact-path hits only)', async () => {
    const store = new GraphDataStore(drillApi());
    await store.loadInitial();
    await store.drillFile('m.py', 2);

    expect(store.mode()).toBe('symbols');
    expect(store.breadcrumb().length).toBe(2);
    expect(store.breadcrumb()[1].path).toBe('m.py');
    // occ:12 belongs to another file — substring path match must not leak it
    expect(store.nodes().map((n) => n.id).sort()).toEqual(['occ:10', 'occ:11']);
    expect(store.edges().length).toBe(1);
    expect(store.fileContext()?.expectedSymbols).toBe(2);
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

  it('reports empty when no occurrences resolve for the file', async () => {
    const api = makeApi({
      searchCode: jasmine.createSpy().and.resolveTo(searchResponse([])),
    });
    const store = new GraphDataStore(api);
    await store.loadInitial();
    await store.drillFile('ghost.py', 3);
    expect(store.phase()).toBe('empty');
    expect(store.mode()).toBe('symbols');
    expect(store.nodes().length).toBe(0);
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
