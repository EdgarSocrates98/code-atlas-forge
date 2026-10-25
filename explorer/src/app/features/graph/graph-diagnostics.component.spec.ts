import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import type { ApiService } from '../../core/api/api.service';
import { GraphDataStore } from './graph-data.store';
import { GraphDiagnosticsComponent } from './graph-diagnostics.component';
import type { GraphCanvasComponent } from './graph-canvas.component';

function makeApi(): ApiService {
  return {
    listRepositories: jasmine
      .createSpy()
      .and.resolveTo({ items: [{ id: 1, identity_key: 'k', root_handle: '/r' }], total: 1 }),
    getGraphOverview: jasmine.createSpy().and.resolveTo({
      snapshot_id: 5,
      repository_id: 1,
      items: [{ path: 'a.py', symbols: 2, language: 'python' }],
      total_files: 1,
      truncated: false,
    }),
    getGraphNeighbors: jasmine
      .createSpy()
      .and.resolveTo({ snapshot_id: 5, nodes: [], edges: [], truncated: false }),
    getSymbolDetail: jasmine.createSpy().and.resolveTo(null),
    getFileOccurrences: jasmine.createSpy().and.resolveTo({ items: [], total: 0 }),
  } as unknown as ApiService;
}

/** Fake canvas exposing exactly what the panel samples. */
function makeCanvas(ratio: number | null): GraphCanvasComponent {
  return {
    layoutBusy: false,
    lastLayoutMs: 42.4,
    cameraRatio: () => ratio,
  } as GraphCanvasComponent;
}

describe('GraphDiagnosticsComponent', () => {
  let store: GraphDataStore;
  let fixture: ComponentFixture<GraphDiagnosticsComponent>;

  beforeEach(async () => {
    store = new GraphDataStore(makeApi());
    await TestBed.configureTestingModule({
      imports: [GraphDiagnosticsComponent],
      providers: [{ provide: GraphDataStore, useValue: store }],
    }).compileComponents();
    fixture = TestBed.createComponent(GraphDiagnosticsComponent);
  });

  it('renders real store counts and the render cap', async () => {
    await store.loadInitial();
    fixture.detectChanges();
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Nós visíveis');
    expect(text).toContain('Nós ocultos');
    expect(text).toContain('Relações visíveis');
    expect(text).toContain('Limite de renderização');
    expect(text).toContain('500'); // DEFAULT_NODE_CAP
    // Exactly one file node came back from the overview fixture.
    expect(text).toMatch(/Nós visíveis\s*1/);
  });

  it('shows "—" for camera-dependent values when no canvas exists', async () => {
    await store.loadInitial();
    fixture.detectChanges();
    // No canvas input → zoom and last-layout stay unmeasurable.
    expect(fixture.componentInstance.zoomText()).toBe('—');
    expect(fixture.componentInstance.lastLayoutText()).toBe('—');
    expect(fixture.componentInstance.layoutBusy()).toBeFalse();
  });

  it('reads live camera/layout values off the canvas while open', fakeAsync(() => {
    fixture.componentRef.setInput('canvas', makeCanvas(0.85));
    fixture.detectChanges();
    // One 500ms sampling window publishes the values.
    tick(600);
    fixture.detectChanges();
    expect(fixture.componentInstance.zoomText()).toBe('0.85');
    expect(fixture.componentInstance.lastLayoutText()).toBe('42 ms');
    expect(fixture.componentInstance.layoutBusy()).toBeFalse();
    expect(fixture.componentInstance.fps()).not.toBeNull();
    fixture.destroy();
  }));

  it('formats the layout-busy state honestly', () => {
    fixture.detectChanges();
    expect((fixture.nativeElement as HTMLElement).textContent ?? '').toContain('ocioso');
    fixture.componentInstance.layoutBusy.set(true);
    fixture.detectChanges();
    expect((fixture.nativeElement as HTMLElement).textContent ?? '').toContain('executando');
  });

  it('stops the rAF sampler on destroy (no permanent profiling)', fakeAsync(() => {
    fixture.detectChanges();
    tick(600);
    const spy = spyOn(window, 'cancelAnimationFrame').and.callThrough();
    fixture.destroy();
    expect(spy).toHaveBeenCalled();
  }));
});
