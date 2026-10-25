import { TestBed } from '@angular/core/testing';
import { ApiService } from '../../core/api/api.service';
import { ApiUnreachableError } from '../../core/api/api.errors';
import type { ApiIndexStatusItem } from '../../core/api/api.service';
import { DiagnosticsComponent } from './diagnostics.component';
import { toCapabilitiesView, toIndexStatusRow } from './diagnostics.vm';
import { PerfLogService } from './perf-log.service';

function makeStatusItem(overrides: Partial<ApiIndexStatusItem> = {}): ApiIndexStatusItem {
  return {
    repository_id: 1,
    root_handle: '/repo',
    ready_snapshots: 2,
    latest_ready: 4,
    counts: {
      file_inventory: 10,
      symbol_occurrences: 200,
      relations: 150,
      evidence: 300,
    },
    ...overrides,
  };
}

describe('diagnostics view-model', () => {
  it('maps index status rows with labelled counts', () => {
    const row = toIndexStatusRow(makeStatusItem());
    expect(row.hasCounts).toBe(true);
    expect(row.counts.map((c) => c.value)).toEqual([10, 200, 150, 300]);
    expect(row.counts[0].label).toBe('Arquivos');
  });

  it('handles a repository without READY snapshots', () => {
    const row = toIndexStatusRow(
      makeStatusItem({ ready_snapshots: 0, latest_ready: null, counts: {} }),
    );
    expect(row.hasCounts).toBe(false);
    expect(row.latestReady).toBeNull();
  });

  it('maps capabilities including the vector flag', () => {
    const on = toCapabilitiesView({
      search: {
        strategies: ['lexical', 'vector'],
        fts5: true,
        vector: true,
        detail: 'ok',
      },
      api_version: 1,
      readonly: true,
    });
    expect(on.vector).toBe(true);
    expect(on.vectorLabel).toBe('Busca vetorial disponível');

    const off = toCapabilitiesView({
      search: { strategies: ['lexical'], fts5: true, vector: false, detail: '' },
      api_version: 1,
      readonly: true,
    });
    expect(off.vector).toBe(false);
    expect(off.vectorLabel).toBe('Busca vetorial indisponível');
  });
});

describe('PerfLogService (in-memory only)', () => {
  it('records entries newest-first and caps the list', async () => {
    const perf = new PerfLogService();
    for (let i = 0; i < 60; i++) {
      perf.record(`call-${i}`, performance.now(), i % 2 === 0);
    }
    expect(perf.entries().length).toBe(50);
    expect(perf.entries()[0].label).toBe('call-59');
    perf.clear();
    expect(perf.entries().length).toBe(0);
  });

  it('times async calls and records failures too', async () => {
    const perf = new PerfLogService();
    const value = await perf.time('ok-call', () => Promise.resolve(42));
    expect(value).toBe(42);
    expect(perf.entries()[0].ok).toBe(true);

    await expectAsync(
      perf.time('bad-call', () => Promise.reject(new Error('boom'))),
    ).toBeRejected();
    expect(perf.entries()[0].label).toBe('bad-call');
    expect(perf.entries()[0].ok).toBe(false);
  });
});

describe('DiagnosticsComponent', () => {
  let api: jasmine.SpyObj<ApiService>;

  beforeEach(async () => {
    api = jasmine.createSpyObj<ApiService>('ApiService', [
      'getHealth',
      'getCapabilities',
      'getIndexStatus',
      'listSnapshots',
    ]);
    api.getHealth.and.resolveTo({ status: 'ok', service: 'code-atlas-api', version: 1 });
    api.getCapabilities.and.resolveTo({
      search: { strategies: ['lexical'], fts5: true, vector: false, detail: '' },
      api_version: 1,
      readonly: true,
    });
    api.getIndexStatus.and.resolveTo({ items: [makeStatusItem()] });
    api.listSnapshots.and.resolveTo({
      items: [
        { id: 4, status: 'READY', commit_sha: 'abc', git_ref: 'main', created_at: '2026-01-02' },
      ],
    });

    await TestBed.configureTestingModule({
      imports: [DiagnosticsComponent],
      providers: [{ provide: ApiService, useValue: api }],
    }).compileComponents();
  });

  it('renders health, capabilities and index rows', async () => {
    const fixture = TestBed.createComponent(DiagnosticsComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const component = fixture.componentInstance;
    expect(component.phase()).toBe('ready');
    expect(component.healthState()).toBe('ok');

    const el = fixture.nativeElement as HTMLElement;
    expect(el.textContent).toContain('API local online');
    expect(el.textContent).toContain('Busca vetorial indisponível');
    expect(el.textContent).toContain('/repo');
    expect(el.textContent).toContain('200');
  });

  it('expands the snapshot list per repository', async () => {
    const fixture = TestBed.createComponent(DiagnosticsComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;

    await component.toggleSnapshots(1);
    expect(api.listSnapshots).toHaveBeenCalledWith(1, jasmine.anything());
    expect(component.snapshots().length).toBe(1);
    await component.toggleSnapshots(1);
    expect(component.expandedRepo()).toBeNull();
  });

  it('records latencies of its own calls in the perf log', async () => {
    const fixture = TestBed.createComponent(DiagnosticsComponent);
    const perf = fixture.componentInstance.perf;
    perf.clear();
    fixture.detectChanges();
    await fixture.whenStable();

    const labels = perf.entries().map((e) => e.label);
    expect(labels).toContain('GET /health');
    expect(labels).toContain('GET /capabilities');
    expect(labels).toContain('GET /index/status');
  });

  it('shows offline state when health probe fails', async () => {
    api.getHealth.and.rejectWith(new ApiUnreachableError(new TypeError('down')));
    const fixture = TestBed.createComponent(DiagnosticsComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const component = fixture.componentInstance;
    expect(component.phase()).toBe('offline');
    expect(component.healthState()).toBe('down');
    const el = fixture.nativeElement as HTMLElement;
    expect(el.textContent).toContain('API local indisponível');
  });
});
