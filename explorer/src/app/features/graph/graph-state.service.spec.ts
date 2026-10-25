import { GraphStateService, GraphViewState } from './graph-state.service';

function view(overrides: Partial<GraphViewState> = {}): GraphViewState {
  return {
    mode: 'architecture',
    camera: { x: 0.5, y: -0.25, ratio: 0.8 },
    selectedNodeId: 'file:a.py',
    selectedEdgeId: null,
    pinnedIds: ['file:a.py'],
    ...overrides,
  };
}

describe('GraphStateService', () => {
  it('keys views by repository + snapshot + mode', () => {
    expect(GraphStateService.key(1, 7, 'architecture')).toBe('r1/s7/architecture');
    expect(GraphStateService.key(1, 7, 'symbols')).toBe('r1/s7/symbols');
    expect(GraphStateService.key(1, null, 'architecture')).toBe('r1/s?/architecture');
    expect(GraphStateService.key(null, null, 'architecture')).toBe('r?/s?/architecture');
  });

  it('saves and consumes a view exactly once', () => {
    const svc = new GraphStateService();
    const key = GraphStateService.key(1, 7, 'architecture');
    svc.save(key, view());
    const first = svc.consume(key);
    expect(first?.camera).toEqual({ x: 0.5, y: -0.25, ratio: 0.8 });
    expect(first?.selectedNodeId).toBe('file:a.py');
    expect(svc.consume(key)).toBeUndefined(); // already consumed
  });

  it('keeps modes isolated under the same repo+snapshot', () => {
    const svc = new GraphStateService();
    svc.save(GraphStateService.key(1, 7, 'architecture'), view({ mode: 'architecture' }));
    svc.save(
      GraphStateService.key(1, 7, 'symbols'),
      view({ mode: 'symbols', camera: { x: 9, y: 9, ratio: 2 } }),
    );
    const symbols = svc.consume(GraphStateService.key(1, 7, 'symbols'));
    expect(symbols?.camera?.x).toBe(9);
    // the architecture record survives the symbols consume
    const arch = svc.consume(GraphStateService.key(1, 7, 'architecture'));
    expect(arch?.camera?.x).toBe(0.5);
  });

  it('evicts the oldest key past the bound', () => {
    const svc = new GraphStateService();
    for (let i = 0; i < 12; i++) {
      svc.save(GraphStateService.key(i, 7, 'architecture'), view());
    }
    // first four keys were evicted (cap 8)
    expect(svc.consume(GraphStateService.key(0, 7, 'architecture'))).toBeUndefined();
    expect(svc.consume(GraphStateService.key(3, 7, 'architecture'))).toBeUndefined();
    expect(svc.consume(GraphStateService.key(4, 7, 'architecture'))).toBeDefined();
    expect(svc.consume(GraphStateService.key(11, 7, 'architecture'))).toBeDefined();
  });

  it('re-saving a key refreshes recency', () => {
    const svc = new GraphStateService();
    const key0 = GraphStateService.key(0, 7, 'architecture');
    for (let i = 0; i < 8; i++) {
      svc.save(GraphStateService.key(i, 7, 'architecture'), view());
    }
    svc.save(key0, view({ selectedNodeId: 'file:recent.py' })); // refresh
    svc.save(GraphStateService.key(99, 7, 'architecture'), view()); // evicts key1, not key0
    const restored = svc.consume(key0);
    expect(restored?.selectedNodeId).toBe('file:recent.py');
  });
});
