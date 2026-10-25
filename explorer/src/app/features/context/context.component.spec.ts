import { TestBed } from '@angular/core/testing';
import { ApiService } from '../../core/api/api.service';
import { ApiUnreachableError } from '../../core/api/api.errors';
import type { ApiContextPack } from '../../core/api/api.service';
import { ContextComponent } from './context.component';
import { packToMarkdown, toBudgetView, toPackView } from './context.vm';

function makePack(overrides: Partial<ApiContextPack> = {}): ApiContextPack {
  return {
    manifest: {
      schema_version: 2,
      repository: '/repo',
      snapshot_id: 4,
      level: 'focus',
      estimator: 'est-v1',
      tokens_estimated: true,
      selected: 2,
      omitted: 1,
      truncated: false,
      freshness: 'snapshot-4',
      ranking_explanation: 'prioridade por evidência',
      tokens_payload: 300,
    },
    budget: {
      limit: 4000,
      reserved_meta: 100,
      reserved_evidence: 50,
      reserved_diagnostics: 50,
      available_items: 3800,
      estimator: 'est-v1',
    },
    items: [
      {
        item_id: 'i1',
        kind: 'symbol',
        priority: 1,
        title: 'pkg.a',
        body: 'def a(): ...',
        est_tokens: 500,
        symbol_key: 'python:function:pkg.a',
        path: 'pkg/a.py',
        evidence: [{ kind: 'occurrence', ref: 'occ:1', detail: '' }],
        expandable: true,
      },
    ],
    selected_symbols: ['python:function:pkg.a'],
    relevant_files: ['pkg/a.py'],
    dependencies: [{ kind: 'relation', ref: 'rel:9', detail: 'calls' }],
    unresolved_refs: ['ext.lib'],
    expansion_refs: [{ ref_id: 'sym:2', kind: 'symbol', reason: 'vizinho direto' }],
    diagnostics: {
      codes: [],
      messages: [],
      insufficient_budget: false,
      mandatory_context_omitted: false,
      suggested_minimum_budget: 0,
    },
    ...overrides,
  };
}

describe('context pack view-model', () => {
  it('computes budget usage from reserves + item estimates', () => {
    const budget = toBudgetView(makePack());
    expect(budget.limit).toBe(4000);
    expect(budget.reservedTotal).toBe(200);
    expect(budget.itemsEstimate).toBe(500);
    expect(budget.usedPercent).toBe(18); // (200+500)/4000
    expect(budget.estimated).toBe(true);
    expect(budget.overBudget).toBe(false);
  });

  it('flags over-budget packs', () => {
    const pack = makePack();
    pack.budget = { ...pack.budget, limit: 600 };
    const budget = toBudgetView(pack);
    expect(budget.usedPercent).toBe(100);
    expect(budget.overBudget).toBe(true);
  });

  it('maps diagnostics flags to the view', () => {
    const pack = makePack();
    pack.diagnostics = {
      codes: ['budget-small'],
      messages: ['orçamento pequeno'],
      insufficient_budget: true,
      mandatory_context_omitted: true,
      suggested_minimum_budget: 8000,
    };
    const view = toPackView(pack);
    expect(view.insufficientBudget).toBe(true);
    expect(view.mandatoryContextOmitted).toBe(true);
    expect(view.suggestedMinimumBudget).toBe(8000);
  });

  it('renders a deterministic markdown export', () => {
    const md = packToMarkdown(makePack());
    expect(md).toContain('# Context Pack');
    expect(md).toContain('`python:function:pkg.a`');
    expect(md).toContain('`pkg/a.py`');
    expect(md).toContain('Dependências');
    expect(md).toContain('`ext.lib`');
    expect(md).toContain('`sym:2`');
  });
});

describe('ContextComponent', () => {
  let api: jasmine.SpyObj<ApiService>;

  beforeEach(async () => {
    api = jasmine.createSpyObj<ApiService>('ApiService', [
      'listRepositories',
      'buildContextPack',
    ]);
    api.listRepositories.and.resolveTo({ items: [], total: 0 });
    api.buildContextPack.and.resolveTo(makePack());

    await TestBed.configureTestingModule({
      imports: [ContextComponent],
      providers: [{ provide: ApiService, useValue: api }],
    }).compileComponents();
  });

  it('sends task/level/budget and renders the pack', async () => {
    const fixture = TestBed.createComponent(ContextComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;

    component.form.task = 'entender retry';
    component.form.level = 'deep';
    component.form.budgetTokens = 8000;
    component.form.maxSymbols = 25;
    await component.build();
    fixture.detectChanges();

    expect(api.buildContextPack).toHaveBeenCalledWith(
      jasmine.objectContaining({
        task: 'entender retry',
        level: 'deep',
        budget_tokens: 8000,
        max_symbols: 25,
      }),
      jasmine.anything(),
    );
    expect(component.phase()).toBe('ready');

    const el = fixture.nativeElement as HTMLElement;
    expect(el.textContent).toContain('Símbolos selecionados (1)');
    expect(el.textContent).toContain('estimados');
    expect(el.textContent).toContain('Referências não resolvidas');
  });

  it('omits repository_root when the default option is selected', async () => {
    const fixture = TestBed.createComponent(ContextComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;
    component.form.task = 'x';
    await component.build();
    const body = api.buildContextPack.calls.mostRecent().args[0];
    expect(body.repository_root).toBeUndefined();
  });

  it('stays offline when the API is unreachable', async () => {
    api.buildContextPack.and.rejectWith(new ApiUnreachableError(new TypeError('down')));
    const fixture = TestBed.createComponent(ContextComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;
    component.form.task = 'x';
    await component.build();
    expect(component.phase()).toBe('offline');
  });
});
