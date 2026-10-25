import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { ApiService } from '../../../core/api/api.service';
import { ApiUnreachableError } from '../../../core/api/api.errors';
import type {
  ApiDomainEdge,
  ApiDomainEntity,
} from '../../../core/api/api.service';
import { PipelinesComponent } from './pipelines.component';
import { buildPipelinesView, jobDisplayName } from './pipelines.vm';

function entity(overrides: Partial<ApiDomainEntity>): ApiDomainEntity {
  return {
    id: 1,
    domain: 'cicd',
    kind: 'cicd_workflow',
    qualified_name: '.github/workflows/ci.yml',
    name: 'ci',
    catalog: '',
    namespace: '',
    environment: '',
    attributes: {},
    occurrences: 1,
    first_occurrence_id: 1,
    path: '.github/workflows/ci.yml',
    line: 1,
    extraction_status: 'ready',
    ...overrides,
  };
}

function job(id: number, wfQName: string, name: string): ApiDomainEntity {
  return entity({
    id,
    kind: 'cicd_job',
    qualified_name: `${wfQName}/jobs/${name}`,
    name,
    attributes: { runs_on: 'ubuntu-latest' },
  });
}

function dep(fromId: number, toId: number | null, label?: string): ApiDomainEdge {
  return {
    id: fromId * 100 + (toId ?? 0),
    relation_id: fromId,
    snapshot_id: 2,
    type: 'DEPENDS_ON',
    resolution_status: toId === null ? 'unresolved' : 'resolved',
    confidence_class: 'resolved',
    resolver: 'cicd-needs',
    evidence_ids: [],
    from: { ref: `de:${fromId}`, endpoint: 'entity', entity_id: fromId },
    to:
      toId === null
        ? { ref: `unresolved:${label}`, endpoint: 'unresolved', label }
        : { ref: `de:${toId}`, endpoint: 'entity', entity_id: toId },
  };
}

const WF = '.github/workflows/ci.yml';

describe('buildPipelinesView', () => {
  it('groups jobs under workflows and assigns DAG levels', () => {
    const wf = entity({ id: 1 });
    const build = job(2, WF, 'build');
    const test = job(3, WF, 'test');
    const edges = [dep(3, 2)];
    const [view] = buildPipelinesView([wf, build, test], edges);
    expect(view.jobs.map((j) => j.job.name).sort()).toEqual(['build', 'test']);
    const byName = Object.fromEntries(
      view.jobs.map((j) => [j.job.name, j]),
    );
    expect(byName['build'].level).toBe(0);
    expect(byName['test'].level).toBe(1);
    expect(byName['test'].needs).toEqual(['build']);
  });

  it('keeps unresolved needs textual — never resolves them to jobs', () => {
    const wf = entity({ id: 1 });
    const test = job(3, WF, 'test');
    const [view] = buildPipelinesView([wf, test], [dep(3, null, 'lint')]);
    expect(view.jobs[0].unresolvedNeeds).toEqual(['lint']);
    expect(view.jobs[0].needs).toEqual([]);
  });

  it('detects needs cycles and leaves levels null', () => {
    const wf = entity({ id: 1 });
    const a = job(2, WF, 'a');
    const b = job(3, WF, 'b');
    const [view] = buildPipelinesView([wf, a, b], [dep(2, 3), dep(3, 2)]);
    expect(view.cyclic).toBeTrue();
    expect(view.jobs.every((j) => j.level === null)).toBeTrue();
  });

  it('strips the workflow prefix for display names', () => {
    expect(jobDisplayName(job(1, WF, 'build'))).toBe('build');
  });
});

describe('PipelinesComponent', () => {
  let api: jasmine.SpyObj<ApiService>;

  beforeEach(async () => {
    localStorage.clear();
    api = jasmine.createSpyObj<ApiService>('ApiService', [
      'listRepositories',
      'listSnapshots',
      'getDomainEntities',
      'getDomainRelations',
    ]);
    api.listRepositories.and.resolveTo({
      items: [{ id: 1, identity_key: 'k', root_handle: '/repo' }],
      total: 1,
    });
    api.listSnapshots.and.resolveTo({
      items: [
        { id: 2, status: 'READY', commit_sha: 'abc', git_ref: 'main', created_at: '2026-01-01' },
      ],
    });
    api.getDomainEntities.and.resolveTo({
      repository_id: 1,
      snapshot_id: 2,
      items: [
        entity({ id: 1, attributes: { name: 'CI', platform: 'github-actions', triggers: 'push,pull_request' } }),
        job(2, WF, 'build'),
        job(3, WF, 'test'),
      ],
      total: 3,
      limit: 200,
      offset: 0,
      truncated: false,
    });
    api.getDomainRelations.and.resolveTo({
      repository_id: 1,
      snapshot_id: 2,
      edges: [dep(3, 2), dep(3, null, 'lint')],
      total: 2,
      limit: 200,
      offset: 0,
      truncated: false,
    });

    await TestBed.configureTestingModule({
      imports: [PipelinesComponent],
      providers: [provideRouter([]), { provide: ApiService, useValue: api }],
    }).compileComponents();
  });

  it('renders workflows, triggers, jobs, resolved + unresolved needs', async () => {
    const fixture = TestBed.createComponent(PipelinesComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    // Angular 19+: signal-based effects are not flushed by whenStable —
    // tick() runs them, then whenStable drains the deferred load().
    TestBed.tick();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(api.getDomainEntities).toHaveBeenCalledWith(
      jasmine.objectContaining({ domain: 'cicd', repositoryId: 1 }),
      jasmine.anything(),
    );
    const relParams = api.getDomainRelations.calls.mostRecent().args[0];
    expect(relParams?.type).toContain('DEPENDS_ON');

    const el = fixture.nativeElement as HTMLElement;
    expect(el.textContent).toContain('CI');
    expect(el.textContent).toContain('push');
    expect(el.textContent).toContain('build');
    expect(el.textContent).toContain('test');
    expect(el.textContent).toContain('lint');
    expect(el.textContent).toContain('não resolvido');
  });

  it('shows the honest empty state when no workflows exist', async () => {
    api.getDomainEntities.and.resolveTo({
      repository_id: 1,
      snapshot_id: 2,
      items: [],
      total: 0,
      limit: 200,
      offset: 0,
      truncated: false,
    });
    api.getDomainRelations.and.resolveTo({
      repository_id: 1,
      snapshot_id: 2,
      edges: [],
      total: 0,
      limit: 200,
      offset: 0,
      truncated: false,
    });
    const fixture = TestBed.createComponent(PipelinesComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    // Angular 19+: signal-based effects are not flushed by whenStable —
    // tick() runs them, then whenStable drains the deferred load().
    TestBed.tick();
    await fixture.whenStable();
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).textContent).toContain(
      'Nenhum workflow CI/CD',
    );
  });

  it('surfaces offline state', async () => {
    api.getDomainEntities.and.rejectWith(
      new ApiUnreachableError(new TypeError('down')),
    );
    const fixture = TestBed.createComponent(PipelinesComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    TestBed.tick();
    await fixture.whenStable();
    expect(fixture.componentInstance.phase()).toBe('offline');
  });
});
