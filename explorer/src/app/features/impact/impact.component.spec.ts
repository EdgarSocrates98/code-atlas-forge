import { TestBed } from '@angular/core/testing';
import { ApiService } from '../../core/api/api.service';
import { ApiUnreachableError } from '../../core/api/api.errors';
import type {
  ApiImpactCandidate,
  ApiImpactResult,
  ApiSymbolChange,
} from '../../core/api/api.service';
import { ImpactComponent } from './impact.component';
import {
  groupBlastCandidates,
  groupSymbolChanges,
  isPartialAnalysis,
  toImpactView,
} from './impact.vm';

function makeChange(overrides: Partial<ApiSymbolChange> = {}): ApiSymbolChange {
  return {
    symbol_key: 'python:function:pkg.a',
    qualified_name: 'pkg.a',
    language: 'python',
    kind: 'function',
    change: 'body_changed',
    strength: 'verified',
    old_signature: 'def a()',
    new_signature: 'def a(x)',
    old_path: 'pkg/a.py',
    new_path: 'pkg/a.py',
    old_occurrence_id: 1,
    new_occurrence_id: 2,
    detail: '',
    ...overrides,
  };
}

function makeImpact(overrides: Partial<ApiImpactCandidate> = {}): ApiImpactCandidate {
  return {
    symbol_key: 'python:function:pkg.b',
    qualified_name: 'pkg.b',
    language: 'python',
    kind: 'function',
    impact: 'transitive',
    strength: 'possible',
    path: {
      origin_symbol_key: 'python:function:pkg.a',
      affected_symbol_key: 'python:function:pkg.b',
      hops: 2,
      relation_ids: [1, 2],
      intermediate_keys: ['python:function:pkg.m'],
      resolution_statuses: ['verified', 'possible'],
      fully_verified: false,
      reason: 'cadeia via pkg.m',
    },
    source_path: 'pkg/b.py',
    occurrence_id: 9,
    consumer_state: 'still_present',
    ...overrides,
  };
}

function makeResult(overrides: Partial<ApiImpactResult> = {}): ApiImpactResult {
  return {
    schema_version: 1,
    analyzer_version: 'impact-v1',
    repository: '/repo',
    base_revision: 'HEAD~1',
    target_revision: 'HEAD',
    base_snapshot_id: 3,
    target_snapshot_id: 4,
    change_set: null,
    symbol_changes: [makeChange()],
    contract_changes: [],
    impacts: [makeImpact()],
    test_candidates: [
      {
        test_file: 'tests/test_a.py',
        test_symbol: 'test_a',
        relationship: 'direct',
        reason: 'testa pkg.a diretamente',
        strength: 'verified',
        occurrence_id: 30,
        revision: 'target',
      },
    ],
    risk: {
      category: 'moderate',
      uncertainty: 'low',
      reasons: ['1 mudança de assinatura'],
      formula_version: 'risk-rules-v1',
    },
    unknown_coverage: [],
    truncated: false,
    diagnostics: [],
    ...overrides,
  };
}

describe('impact view-model', () => {
  it('detects partial analysis only from binding diagnostics', () => {
    expect(isPartialAnalysis([{ code: 'partial-analysis', message: 'x' }])).toBe(true);
    expect(isPartialAnalysis([{ code: 'dirty-worktree', message: 'x' }])).toBe(true);
    expect(
      isPartialAnalysis([{ code: 'revision-binding-mismatch', message: 'x' }]),
    ).toBe(true);
    expect(isPartialAnalysis([{ code: 'something-else', message: 'x' }])).toBe(false);
    expect(isPartialAnalysis([])).toBe(false);
  });

  it('groups symbol changes by kind in canonical order', () => {
    const groups = groupSymbolChanges([
      makeChange({ change: 'body_changed', symbol_key: 'k1' }),
      makeChange({ change: 'added', symbol_key: 'k2' }),
      makeChange({ change: 'removed', symbol_key: 'k3' }),
      makeChange({ change: 'added', symbol_key: 'k4' }),
      makeChange({ change: 'weird_kind', symbol_key: 'k5' }),
    ]);
    expect(groups.map((g) => g.kind)).toEqual([
      'added',
      'removed',
      'body_changed',
      'weird_kind',
    ]);
    expect(groups[0].label).toBe('Adicionados');
    expect(groups[0].items.length).toBe(2);
  });

  it('groups blast candidates by hops, null path last', () => {
    const groups = groupBlastCandidates([
      makeImpact({ symbol_key: 'a' }), // hops 2
      makeImpact({ symbol_key: 'b', path: null }),
      makeImpact({
        symbol_key: 'c',
        path: { ...makeImpact().path!, hops: 1 },
      }),
    ]);
    expect(groups.map((g) => g.hops)).toEqual([1, 2, -1]);
    expect(groups[0].label).toBe('1 salto da origem');
    expect(groups[2].label).toBe('Sem cadeia conhecida');
  });

  it('maps test candidates with revision tag and consumer state labels', () => {
    const view = toImpactView(makeResult());
    const test = view.testCandidates[0];
    expect(test.revisionLabel).toBe('revisão alvo');
    expect(test.relationshipLabel).toBe('direta');
    const blast = view.blastGroups[0].items[0];
    expect(blast.consumerLabel).toBe('presente no alvo');
    expect(blast.fullyVerified).toBe(false);
  });

  it('keeps uncertainty as a separate axis from risk category', () => {
    const view = toImpactView(makeResult());
    expect(view.risk.category).toBe('moderate');
    expect(view.risk.categoryLabel).toBe('Moderado');
    expect(view.risk.uncertaintyLabel).toBe('Baixa');
    expect(view.risk.reasons).toEqual(['1 mudança de assinatura']);
  });

  it('flags semantic-blocked results', () => {
    const view = toImpactView(
      makeResult({ unknown_coverage: ['semantic-blocked'] }),
    );
    expect(view.semanticBlocked).toBe(true);
    const clean = toImpactView(makeResult());
    expect(clean.semanticBlocked).toBe(false);
    expect(clean.partial).toBe(false);
  });
});

describe('ImpactComponent', () => {
  let api: jasmine.SpyObj<ApiService>;

  beforeEach(async () => {
    api = jasmine.createSpyObj<ApiService>('ApiService', [
      'listRepositories',
      'analyzeImpact',
    ]);
    api.listRepositories.and.resolveTo({
      items: [{ id: 1, identity_key: 'k', root_handle: '/repo' }],
      total: 1,
    });
    api.analyzeImpact.and.resolveTo(makeResult());

    await TestBed.configureTestingModule({
      imports: [ImpactComponent],
      providers: [{ provide: ApiService, useValue: api }],
    }).compileComponents();
  });

  it('sends repository_root and flags on analyze', async () => {
    const fixture = TestBed.createComponent(ImpactComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;

    component.form.baseRevision = 'HEAD~1';
    component.form.targetRevision = 'HEAD';
    component.form.symbol = 'pkg.a';
    component.form.knownOnly = true;
    await component.analyze();

    expect(api.analyzeImpact).toHaveBeenCalledWith(
      jasmine.objectContaining({
        repository_root: '/repo',
        base_revision: 'HEAD~1',
        target_revision: 'HEAD',
        symbol: 'pkg.a',
        known_only: true,
        include_possible: true,
      }),
      jasmine.anything(),
    );
    expect(component.phase()).toBe('ready');
  });

  it('renders the PARTIAL banner when binding diagnostics are present', async () => {
    api.analyzeImpact.and.resolveTo(
      makeResult({
        diagnostics: [
          { code: 'partial-analysis', message: 'binding base parcial' },
          { code: 'dirty-worktree', message: 'worktree sujo' },
        ],
      }),
    );
    const fixture = TestBed.createComponent(ImpactComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;
    component.form.baseRevision = 'HEAD~1';
    await component.analyze();
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    const banner = el.querySelector('.impact-result__banner--partial');
    expect(banner?.textContent).toContain('Análise parcial');
    // Partial results must never be presented as verified.
    expect(el.textContent).not.toContain('análise verificada');
  });

  it('goes offline when the API is unreachable', async () => {
    api.analyzeImpact.and.rejectWith(new ApiUnreachableError(new TypeError('down')));
    const fixture = TestBed.createComponent(ImpactComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;
    component.form.baseRevision = 'HEAD~1';
    await component.analyze();
    expect(component.phase()).toBe('offline');
  });
});
