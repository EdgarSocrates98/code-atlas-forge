import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { ApiService } from '../../../core/api/api.service';
import { ApiUnreachableError } from '../../../core/api/api.errors';
import type {
  ApiDomainEntity,
  ApiDomainNeighbors,
} from '../../../core/api/api.service';
import { ContractsComponent } from './contracts.component';
import { buildContractsView } from './contracts.vm';

function entity(overrides: Partial<ApiDomainEntity>): ApiDomainEntity {
  return {
    id: 1,
    domain: 'api',
    kind: 'api_service',
    qualified_name: 'orders-api',
    name: 'orders-api',
    catalog: '',
    namespace: '',
    environment: '',
    attributes: {},
    occurrences: 1,
    first_occurrence_id: 1,
    path: 'openapi/orders.yaml',
    line: 1,
    extraction_status: 'ready',
    ...overrides,
  };
}

describe('buildContractsView', () => {
  it('groups endpoints under their service via namespace and keeps strays', () => {
    const svc = entity({ id: 1, qualified_name: 'orders-api' });
    const ep = entity({
      id: 2,
      kind: 'api_endpoint',
      qualified_name: 'GET /orders',
      namespace: 'orders-api',
      attributes: { method: 'GET', path: '/orders' },
    });
    const stray = entity({
      id: 3,
      kind: 'api_endpoint',
      qualified_name: 'POST /loose',
      namespace: 'other-svc',
    });
    const view = buildContractsView([svc, ep, stray]);
    expect(view.services.length).toBe(1);
    expect(view.services[0].endpoints.map((e) => e.id)).toEqual([2]);
    expect(view.unboundEndpoints.map((e) => e.id)).toEqual([3]);
  });
});

describe('ContractsComponent', () => {
  let api: jasmine.SpyObj<ApiService>;

  const entities: ApiDomainEntity[] = [
    entity({
      id: 1,
      qualified_name: 'orders-api',
      attributes: { title: 'Orders API', version: '2.0' },
    }),
    entity({
      id: 2,
      kind: 'api_endpoint',
      qualified_name: 'GET /orders',
      namespace: 'orders-api',
      attributes: { method: 'GET', path: '/orders' },
    }),
    entity({
      id: 3,
      kind: 'api_contract',
      qualified_name: 'openapi/orders.yaml',
      name: 'orders.yaml',
      path: 'openapi/orders.yaml',
      attributes: { format: 'openapi3', api_version: '2.0' },
    }),
  ];

  beforeEach(async () => {
    localStorage.clear();
    api = jasmine.createSpyObj<ApiService>('ApiService', [
      'listRepositories',
      'listSnapshots',
      'getDomainEntities',
      'getDomainEntityNeighbors',
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
      items: entities,
      total: entities.length,
      limit: 200,
      offset: 0,
      truncated: false,
    });
    const neighbors: ApiDomainNeighbors = {
      repository_id: 1,
      snapshot_id: 2,
      entity: entities[1],
      edges: [
        {
          id: 7,
          relation_id: 7,
          snapshot_id: 2,
          type: 'CALLS_ENDPOINT',
          resolution_status: 'resolved',
          confidence_class: 'syntactic',
          resolver: 'api-http-literals',
          evidence_ids: [11],
          from: {
            ref: 'occ:99',
            endpoint: 'symbol',
            qualified_name: 'billing.fetchOrders',
            path: 'billing/client.py',
          },
          to: {
            ref: 'de:2',
            endpoint: 'entity',
            qualified_name: 'GET /orders',
          },
        },
      ],
      truncated: false,
    };
    api.getDomainEntityNeighbors.and.resolveTo(neighbors);

    await TestBed.configureTestingModule({
      imports: [ContractsComponent],
      providers: [provideRouter([]), { provide: ApiService, useValue: api }],
    }).compileComponents();
  });

  it('loads api entities scoped to the active snapshot and renders groups', async () => {
    const fixture = TestBed.createComponent(ContractsComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const params = api.getDomainEntities.calls.mostRecent().args[0];
    expect(params?.domain).toBe('api');
    expect(params?.repositoryId).toBe(1);

    const el = fixture.nativeElement as HTMLElement;
    expect(el.textContent).toContain('Orders API');
    // method and path render in separate table cells
    expect(el.textContent).toContain('GET');
    expect(el.textContent).toContain('/orders');
    expect(el.textContent).toContain('openapi3');
  });

  it('lazily loads CALLS_ENDPOINT consumers per endpoint', async () => {
    const fixture = TestBed.createComponent(ContractsComponent);
    fixture.detectChanges();
    await fixture.whenStable();

    const component = fixture.componentInstance;
    await component.toggleConsumers(2);
    fixture.detectChanges();

    expect(api.getDomainEntityNeighbors).toHaveBeenCalledWith(
      2,
      'in',
      jasmine.objectContaining({ repositoryId: 1 }),
      jasmine.anything(),
    );
    const el = fixture.nativeElement as HTMLElement;
    expect(el.textContent).toContain('billing.fetchOrders');
  });

  it('shows the honest empty state when no api domain exists', async () => {
    api.getDomainEntities.and.resolveTo({
      repository_id: 1,
      snapshot_id: 2,
      items: [],
      total: 0,
      limit: 200,
      offset: 0,
      truncated: false,
    });
    const fixture = TestBed.createComponent(ContractsComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).textContent).toContain(
      'Nenhum contrato de API',
    );
  });

  it('surfaces offline state', async () => {
    api.getDomainEntities.and.rejectWith(
      new ApiUnreachableError(new TypeError('down')),
    );
    const fixture = TestBed.createComponent(ContractsComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    expect(fixture.componentInstance.phase()).toBe('offline');
  });
});
