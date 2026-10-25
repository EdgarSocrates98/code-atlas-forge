import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { ApiService } from '../../../core/api/api.service';
import { ApiUnreachableError } from '../../../core/api/api.errors';
import type {
  ApiDomainEdge,
  ApiDomainEntity,
} from '../../../core/api/api.service';
import { InfraComponent } from './infra.component';
import { buildInfraView } from './infra.vm';

function entity(overrides: Partial<ApiDomainEntity>): ApiDomainEntity {
  return {
    id: 1,
    domain: 'terraform',
    kind: 'terraform_resource',
    qualified_name: 'aws_s3_bucket.data',
    name: 'aws_s3_bucket.data',
    catalog: '',
    namespace: 'modules/storage',
    environment: '',
    attributes: {},
    occurrences: 1,
    first_occurrence_id: 1,
    path: 'main.tf',
    line: 3,
    extraction_status: 'ready',
    ...overrides,
  };
}

function edge(
  id: number,
  type: string,
  fromId: number,
  toId: number,
): ApiDomainEdge {
  return {
    id,
    relation_id: id,
    snapshot_id: 2,
    type,
    resolution_status: 'resolved',
    confidence_class: 'resolved',
    resolver: 'terraform-explicit-refs',
    evidence_ids: [],
    from: { ref: `de:${fromId}`, endpoint: 'entity', entity_id: fromId },
    to: { ref: `de:${toId}`, endpoint: 'entity', entity_id: toId },
  };
}

describe('buildInfraView', () => {
  it('groups PROVISIONS targets under their module and names stray namespaces', () => {
    const mod = entity({ id: 1, kind: 'terraform_module', qualified_name: 'module.storage', namespace: '' });
    const res = entity({ id: 2 });
    const stray = entity({ id: 3, qualified_name: 'aws_iam_user.x', namespace: 'modules/other' });
    const view = buildInfraView([mod, res, stray], [edge(1, 'PROVISIONS', 1, 2)], false);
    expect(view.modules[0].resources.map((r) => r.id)).toEqual([2]);
    expect(view.namespaceGroups.length).toBe(1);
    expect(view.namespaceGroups[0].title).toBe('modules/other');
    expect(view.namespaceGroups[0].resources.map((r) => r.id)).toEqual([3]);
  });
});

describe('InfraComponent', () => {
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
        entity({ id: 1, kind: 'terraform_module', qualified_name: 'module.storage', namespace: '' }),
        entity({ id: 2, qualified_name: 'aws_s3_bucket.data' }),
        entity({ id: 4, kind: 'iam_role', qualified_name: 'aws_iam_role.etl', attributes: { trusted_services: 'glue.amazonaws.com' } }),
      ],
      total: 3,
      limit: 200,
      offset: 0,
      truncated: false,
    });
    api.getDomainRelations.and.resolveTo({
      repository_id: 1,
      snapshot_id: 2,
      edges: [
        edge(1, 'PROVISIONS', 1, 2),
        {
          ...edge(2, 'ASSUMES_ROLE', 2, 4),
          resolution_status: 'unresolved',
          to: { ref: 'unresolved:role/etl', endpoint: 'unresolved', label: 'role/etl' },
        },
      ],
      total: 2,
      limit: 200,
      offset: 0,
      truncated: false,
    });

    await TestBed.configureTestingModule({
      imports: [InfraComponent],
      providers: [provideRouter([]), { provide: ApiService, useValue: api }],
    }).compileComponents();
  });

  it('loads terraform entities + relations scoped to the snapshot', async () => {
    const fixture = TestBed.createComponent(InfraComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(api.getDomainEntities).toHaveBeenCalledWith(
      jasmine.objectContaining({ domain: 'terraform', repositoryId: 1 }),
      jasmine.anything(),
    );
    const relParams = api.getDomainRelations.calls.mostRecent().args[0];
    expect(relParams?.type).toContain('PROVISIONS');
    expect(relParams?.type).toContain('USES_RESOURCE');

    const el = fixture.nativeElement as HTMLElement;
    expect(el.textContent).toContain('module.storage');
    expect(el.textContent).toContain('aws_s3_bucket.data');
    expect(el.textContent).toContain('aws_iam_role.etl');
    expect(el.textContent).toContain('glue.amazonaws.com');
    // unresolved edge endpoint stays textual
    expect(el.textContent).toContain('role/etl');
    expect(el.textContent).toContain('não resolvido');
  });

  it('shows the honest empty state when no terraform entities exist', async () => {
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
    const fixture = TestBed.createComponent(InfraComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).textContent).toContain(
      'Nenhum recurso Terraform',
    );
  });

  it('surfaces offline state', async () => {
    api.getDomainEntities.and.rejectWith(
      new ApiUnreachableError(new TypeError('down')),
    );
    const fixture = TestBed.createComponent(InfraComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    expect(fixture.componentInstance.phase()).toBe('offline');
  });
});
