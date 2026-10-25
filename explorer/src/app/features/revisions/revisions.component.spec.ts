import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { ApiService } from '../../core/api/api.service';
import { ApiUnreachableError } from '../../core/api/api.errors';
import type { ApiImpactResult } from '../../core/api/api.service';
import { RevisionsComponent } from './revisions.component';

function makeResult(overrides: Partial<ApiImpactResult> = {}): ApiImpactResult {
  return {
    schema_version: 1,
    analyzer_version: 'impact-v1',
    repository: '/repo',
    base_revision: 'abc123',
    target_revision: 'def456',
    base_snapshot_id: 3,
    target_snapshot_id: 4,
    change_set: null,
    symbol_changes: [
      {
        symbol_key: 'python:function:pkg.a',
        qualified_name: 'pkg.a',
        language: 'python',
        kind: 'function',
        change: 'signature_changed',
        strength: 'verified',
        old_signature: 'def a()',
        new_signature: 'def a(x)',
        old_path: 'pkg/a.py',
        new_path: 'pkg/a.py',
        old_occurrence_id: 1,
        new_occurrence_id: 2,
        detail: '',
      },
    ],
    contract_changes: [],
    impacts: [],
    test_candidates: [],
    risk: {
      category: 'low',
      uncertainty: 'low',
      reasons: [],
      formula_version: 'risk-rules-v1',
    },
    unknown_coverage: [],
    truncated: false,
    diagnostics: [],
    ...overrides,
  };
}

describe('RevisionsComponent', () => {
  let api: jasmine.SpyObj<ApiService>;

  beforeEach(async () => {
    api = jasmine.createSpyObj<ApiService>('ApiService', [
      'listRepositories',
      'listSnapshots',
      'analyzeImpact',
    ]);
    api.listRepositories.and.resolveTo({
      items: [{ id: 1, identity_key: 'k', root_handle: '/repo' }],
      total: 1,
    });
    api.listSnapshots.and.resolveTo({
      items: [
        { id: 4, status: 'READY', commit_sha: 'def456sha', git_ref: 'main', created_at: '2026-01-02' },
        { id: 3, status: 'READY', commit_sha: 'abc123sha', git_ref: 'main~1', created_at: '2026-01-01' },
      ],
    });
    api.analyzeImpact.and.resolveTo(makeResult());

    await TestBed.configureTestingModule({
      imports: [RevisionsComponent],
      providers: [provideRouter([]), { provide: ApiService, useValue: api }],
    }).compileComponents();
  });

  it('auto-selects a repository and pre-fills newest READY pair', async () => {
    const fixture = TestBed.createComponent(RevisionsComponent);
    fixture.detectChanges();
    await fixture.whenStable();

    const component = fixture.componentInstance;
    expect(api.listSnapshots).toHaveBeenCalledWith(1, jasmine.anything());
    expect(component.snapshots().length).toBe(2);
    // newest READY → target, previous READY → base
    expect(component.form.targetSnapshotId).toBe(4);
    expect(component.form.baseSnapshotId).toBe(3);
  });

  it('sends snapshot ids (not fabricated revisions) when selectors used', async () => {
    const fixture = TestBed.createComponent(RevisionsComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;

    await component.compare();

    expect(api.analyzeImpact).toHaveBeenCalledWith(
      jasmine.objectContaining({
        repository_root: '/repo',
        base_snapshot_id: 3,
        target_snapshot_id: 4,
      }),
      jasmine.anything(),
    );
    const body = api.analyzeImpact.calls.mostRecent().args[0];
    expect(body.base_revision).toBeUndefined();
    expect(body.target_revision).toBeUndefined();
  });

  it('renders binding status and grouped changes; marks partial explicitly', async () => {
    api.analyzeImpact.and.resolveTo(
      makeResult({ diagnostics: [{ code: 'partial-analysis', message: 'parcial' }] }),
    );
    const fixture = TestBed.createComponent(RevisionsComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;

    await component.compare();
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    const binding = el.querySelector('.revisions__binding');
    expect(binding?.textContent).toContain('snapshot #3');
    expect(binding?.textContent).toContain('PARCIAL');
    expect(el.textContent).toContain('Assinatura alterada');
  });

  it('requires at least one selector/expression per pair', async () => {
    const fixture = TestBed.createComponent(RevisionsComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;

    component.form.baseSnapshotId = null;
    component.form.targetSnapshotId = null;
    expect(component.canCompare()).toBe(false);
    component.form.targetRevision = 'HEAD';
    expect(component.canCompare()).toBe(true);
  });

  it('surfaces offline state when repository probe fails', async () => {
    api.listRepositories.and.rejectWith(new ApiUnreachableError(new TypeError('down')));
    const fixture = TestBed.createComponent(RevisionsComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    expect(fixture.componentInstance.phase()).toBe('offline');
  });
});
