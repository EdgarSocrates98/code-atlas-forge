import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import type { ApiGraphNeighbors, ApiService } from '../../core/api/api.service';
import { GraphDataStore } from './graph-data.store';
import { GraphInspectorComponent } from './graph-inspector.component';
import { GraphNodeVM } from './graph.models';

function makeNode(id: number, overrides: Partial<GraphNodeVM> = {}): GraphNodeVM {
  return {
    id: `occ:${id}`,
    kind: 'symbol',
    label: `fn${id}`,
    qname: `pkg.mod.fn${id}`,
    symbolKind: 'function',
    language: 'python',
    path: 'pkg/mod.py',
    occurrenceId: id,
    symbolId: id * 10,
    span: { start: id, end: id + 1 },
    line: id,
    col: 1,
    parseStatus: 'ok',
    symbolCount: null,
    fileId: null,
    x: 0,
    y: 0,
    pinned: false,
    expanded: false,
    ...overrides,
  };
}

/**
 * ApiService stub whose neighbors endpoint hands the store a small graph:
 * occ:1 → occ:2 (calls/verified, evidence [9]) and occ:2 → occ:3
 * (imports/unresolved).
 */
function makeApi(): ApiService {
  const detail = {
    ...{
      id: 'occ:1',
      occurrence_id: 1,
      symbol_id: 10,
      qname: 'pkg.mod.fn1',
      kind: 'function',
      language: 'python',
      path: 'pkg/mod.py',
      span: { start: 1, end: 2 },
      parse_status: 'ok',
    },
    relations_summary: [
      { type: 'calls', resolution_status: 'verified', n: 1 },
      { type: 'references', resolution_status: 'possible', n: 4 },
    ],
  };
  const neighbors: ApiGraphNeighbors = {
    snapshot_id: 5,
    nodes: [
      {
        id: 'occ:1',
        occurrence_id: 1,
        symbol_id: 10,
        qname: 'pkg.mod.fn1',
        kind: 'function',
        language: 'python',
        path: 'pkg/mod.py',
        span: { start: 1, end: 2 },
        parse_status: 'ok',
      },
      {
        id: 'occ:2',
        occurrence_id: 2,
        symbol_id: 20,
        qname: 'pkg.mod.fn2',
        kind: 'function',
        language: 'python',
        path: 'pkg/mod.py',
        span: { start: 3, end: 4 },
        parse_status: 'ok',
      },
      {
        id: 'occ:3',
        occurrence_id: 3,
        symbol_id: 30,
        qname: 'pkg.util.helper',
        kind: 'function',
        language: 'python',
        path: 'pkg/util.py',
        span: { start: 5, end: 6 },
        parse_status: 'ok',
      },
    ],
    edges: [
      {
        id: 1,
        from_id: 1,
        to_id: 2,
        type: 'calls',
        resolution_status: 'verified',
        confidence_class: 'resolved',
        resolver: 'scip',
        evidence_ids: [9],
      },
      {
        id: 2,
        from_id: 2,
        to_id: 3,
        type: 'imports',
        resolution_status: 'unresolved',
        confidence_class: 'heuristic',
        resolver: 'syntax',
      },
      {
        id: 3,
        from_id: 1,
        to_id: null,
        to_unresolved: 'pkg.missing.Symbol',
        type: 'references',
        resolution_status: 'unresolved',
        resolver: 'syntax',
      },
    ],
    truncated: false,
  };
  return {
    listRepositories: jasmine
      .createSpy()
      .and.resolveTo({ items: [{ id: 1, identity_key: 'k', root_handle: '/r' }], total: 1 }),
    getSymbolDetail: jasmine.createSpy('getSymbolDetail').and.resolveTo(detail),
    getGraphNeighbors: jasmine.createSpy('getGraphNeighbors').and.resolveTo(neighbors),
    getGraphOverview: jasmine.createSpy().and.resolveTo({
      snapshot_id: 5,
      repository_id: 1,
      items: [],
      total_files: 0,
      truncated: false,
    }),
    searchCode: jasmine.createSpy().and.resolveTo({ items: [], next_cursor: null }),
  } as unknown as ApiService;
}

describe('GraphInspectorComponent — data mapping', () => {
  let store: GraphDataStore;
  let fixture: ComponentFixture<GraphInspectorComponent>;

  beforeEach(async () => {
    store = new GraphDataStore(makeApi());
    await TestBed.configureTestingModule({
      imports: [GraphInspectorComponent],
      providers: [provideRouter([]), { provide: GraphDataStore, useValue: store }],
    }).compileComponents();
    fixture = TestBed.createComponent(GraphInspectorComponent);
    await store.startAtSymbol('1');
    fixture.detectChanges();
  });

  it('shows the hint when nothing is selected', () => {
    store.clearSelection();
    fixture.detectChanges();
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Selecione um nó');
  });

  it('maps node fields into the inspector (label, qname, kind, path, span)', async () => {
    store.selectNode('occ:1');
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('fn1');
    expect(text).toContain('pkg.mod.fn1');
    expect(text).toContain('function');
    expect(text).toContain('pkg/mod.py');
    expect(text).toContain('1 – 2');
    expect(text).toContain('#5'); // snapshot id
    expect(text).toContain('Copiar');
    // relations_summary from the detail endpoint
    expect(text).toContain('calls');
    expect(text).toContain('Verificado');
    expect(text).toContain('× 4');
  });

  it('maps edge fields into the inspector (type, endpoints, status, confidence, resolver, evidence)', async () => {
    store.selectEdge('edge:1');
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('edge:1');
    expect(text).toContain('calls');
    expect(text).toContain('fn1');
    expect(text).toContain('fn2');
    expect(text).toContain('Verificado');
    expect(text).toContain('verified'); // raw resolution_status beside the badge
    expect(text).toContain('resolved'); // confidence_class
    expect(text).toContain('scip');
    expect(text).toContain('9'); // evidence_ids rendered when present
  });

  it('renders an explicit empty state when the edge carries no evidence ids', async () => {
    store.selectEdge('edge:2');
    fixture.detectChanges();
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('imports');
    expect(text).toContain('Não resolvido');
    expect(text).toContain('heuristic'); // confidence_class when present
    expect(text).toContain('Sem evidência registrada');
  });

  it('shows the unresolved target name instead of a phantom node link', async () => {
    store.selectEdge('edge:3');
    fixture.detectChanges();
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Alvo não resolvido');
    expect(text).toContain('pkg.missing.Symbol');
  });

  it('endpoint buttons jump selection to the node', () => {
    store.selectEdge('edge:1');
    fixture.detectChanges();
    const btn = fixture.nativeElement.querySelector('.linklike') as HTMLButtonElement;
    btn.click();
    expect(store.selectedNodeId()).toBe('occ:1');
  });
});
