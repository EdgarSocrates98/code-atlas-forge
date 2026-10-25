import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { ApiService } from '../../../core/api/api.service';
import { ApiUnreachableError } from '../../../core/api/api.errors';
import type {
  ApiDomainEntity,
  ApiDomainLineage,
  ApiDomainLineageNode,
} from '../../../core/api/api.service';
import { ExplorerContextService } from '../../../core/state/explorer-context.service';
import { LineageComponent } from './lineage.component';
import { toLineageView } from './lineage.vm';

function entity(overrides: Partial<ApiDomainEntity>): ApiDomainEntity {
  return {
    id: 1,
    domain: 'sql',
    kind: 'data_table',
    qualified_name: 'analytics.orders',
    name: 'orders',
    catalog: '',
    namespace: 'analytics',
    environment: '',
    attributes: {},
    occurrences: 1,
    first_occurrence_id: 10,
    path: 'models/orders.sql',
    line: 1,
    extraction_status: 'ready',
    ...overrides,
  };
}

function node(
  ref: string,
  lane: ApiDomainLineageNode['lane'],
  distance: number,
  overrides: Partial<ApiDomainLineageNode> = {},
): ApiDomainLineageNode {
  return {
    ref,
    endpoint: 'entity',
    qualified_name: ref,
    entity_kind: 'data_table',
    domain: 'sql',
    lane,
    distance,
    ...overrides,
  };
}

function lineage(overrides: Partial<ApiDomainLineage> = {}): ApiDomainLineage {
  return {
    repository_id: 1,
    snapshot_id: 2,
    root: node('de:1', 'root', 0),
    direction: 'both',
    depth: 2,
    nodes: [
      node('de:2', 'upstream', 1, { qualified_name: 'staging.orders' }),
      node('de:3', 'downstream', 1, {
        qualified_name: 'marts.daily_revenue',
      }),
      node('unresolved:s3://x', 'downstream', 2, {
        endpoint: 'unresolved',
        label: 's3://x',
        qualified_name: undefined,
      }),
    ],
    edges: [
      {
        id: 1,
        relation_id: 1,
        snapshot_id: 2,
        type: 'DERIVES_FROM',
        resolution_status: 'resolved',
        confidence_class: 'resolved',
        resolver: 'sql-lineage',
        evidence_ids: [5],
        from: { ref: 'de:1', endpoint: 'entity', qualified_name: 'analytics.orders' },
        to: { ref: 'de:2', endpoint: 'entity', qualified_name: 'staging.orders' },
      },
    ],
    truncated: false,
    ...overrides,
  };
}

describe('toLineageView', () => {
  it('splits nodes into lanes ordered by distance', () => {
    const res = lineage({
      nodes: [
        node('de:9', 'upstream', 2, { qualified_name: 'far' }),
        node('de:2', 'upstream', 1, { qualified_name: 'near' }),
        node('de:3', 'downstream', 1),
      ],
    });
    const view = toLineageView(res);
    expect(view.upstream.map((n) => n.ref)).toEqual(['de:2', 'de:9']);
    expect(view.downstream.map((n) => n.ref)).toEqual(['de:3']);
    expect(view.truncated).toBeFalse();
  });
});

describe('LineageComponent', () => {
  let api: jasmine.SpyObj<ApiService>;

  beforeEach(async () => {
    localStorage.clear();
    api = jasmine.createSpyObj<ApiService>('ApiService', [
      'listRepositories',
      'listSnapshots',
      'getDomainEntities',
      'getDomainLineage',
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
      items: [entity({}), entity({ id: 2, qualified_name: 'staging.orders', name: 'orders' })],
      total: 2,
      limit: 200,
      offset: 0,
      truncated: false,
    });
    api.getDomainLineage.and.resolveTo(lineage());

    await TestBed.configureTestingModule({
      imports: [LineageComponent],
      providers: [provideRouter([]), { provide: ApiService, useValue: api }],
    }).compileComponents();
  });

  it('lists data entities and loads lineage on selection', async () => {
    const fixture = TestBed.createComponent(LineageComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(api.getDomainEntities).toHaveBeenCalled();
    const params = api.getDomainEntities.calls.mostRecent().args[0];
    expect(params?.kind).toContain('data_table');
    expect(params?.repositoryId).toBe(1);

    // pin a snapshot — deep-link/pin context must reach the API call
    const ctx = TestBed.inject(ExplorerContextService);
    await ctx.init();
    ctx.selectSnapshot(ctx.snapshots()[0]);
    await fixture.whenStable();

    const component = fixture.componentInstance;
    await component.selectEntity(1);
    await fixture.whenStable();
    fixture.detectChanges();

    expect(api.getDomainLineage).toHaveBeenCalledWith(
      1,
      'both',
      undefined,
      jasmine.objectContaining({ repositoryId: 1, snapshotId: 2 }),
      jasmine.anything(),
    );
    const el = fixture.nativeElement as HTMLElement;
    expect(el.textContent).toContain('staging.orders');
    expect(el.textContent).toContain('marts.daily_revenue');
    expect(el.textContent).toContain('s3://x');
    expect(el.textContent).toContain('não resolvido');
  });

  it('shows the honest empty state when no data entities exist', async () => {
    api.getDomainEntities.and.resolveTo({
      repository_id: 1,
      snapshot_id: 2,
      items: [],
      total: 0,
      limit: 200,
      offset: 0,
      truncated: false,
    });
    const fixture = TestBed.createComponent(LineageComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    expect(el.textContent).toContain('Nenhuma entidade de dados');
  });

  it('surfaces offline state when the API is unreachable', async () => {
    api.getDomainEntities.and.rejectWith(
      new ApiUnreachableError(new TypeError('down')),
    );
    const fixture = TestBed.createComponent(LineageComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    expect(fixture.componentInstance.phase()).toBe('offline');
  });
});
