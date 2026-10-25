import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { ApiService } from '../../../core/api/api.service';
import { ApiUnreachableError } from '../../../core/api/api.errors';
import { FederationComponent } from './federation.component';

describe('FederationComponent', () => {
  let api: jasmine.SpyObj<ApiService>;

  beforeEach(async () => {
    api = jasmine.createSpyObj<ApiService>('ApiService', [
      'listFederationWorkspaces',
      'getFederationWorkspace',
      'getFederationView',
    ]);
    api.listFederationWorkspaces.and.resolveTo({ items: [], total: 0 });
    api.getFederationWorkspace.and.resolveTo({
      id: 1,
      name: 'platform',
      created_at: '2026-01-01',
      members: [
        {
          repository_id: 1,
          pinned_snapshot_id: 10,
          added_at: '2026-01-01',
          root_handle: '/repos/alpha',
          identity_key: 'key-alpha',
        },
      ],
    });
    api.getFederationView.and.resolveTo({
      workspace_id: 1,
      workspace: 'platform',
      view_id: 'view-1',
      members: [
        {
          repository_id: 1,
          snapshot_id: 10,
          pinned: true,
          resolution: 'pin-override',
          snapshot_status: 'READY',
        },
      ],
    });

    await TestBed.configureTestingModule({
      imports: [FederationComponent],
      providers: [provideRouter([]), { provide: ApiService, useValue: api }],
    }).compileComponents();
  });

  it('shows the honest not-configured state when no workspaces exist', async () => {
    const fixture = TestBed.createComponent(FederationComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).textContent).toContain(
      'Federação não configurada',
    );
  });

  it('auto-selects a single workspace and renders members with pins', async () => {
    api.listFederationWorkspaces.and.resolveTo({
      items: [{ id: 1, name: 'platform', created_at: '2026-01-01', members: 1 }],
      total: 1,
    });
    const fixture = TestBed.createComponent(FederationComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(api.getFederationWorkspace).toHaveBeenCalledWith(1, jasmine.anything());
    expect(api.getFederationView).toHaveBeenCalledWith(1, jasmine.anything());

    const el = fixture.nativeElement as HTMLElement;
    expect(el.textContent).toContain('/repos/alpha');
    expect(el.textContent).toContain('key-alpha');
    expect(el.textContent).toContain('pin-override');
    expect(el.textContent).toContain('READY');
  });

  it('loads detail only when a workspace is selected', async () => {
    api.listFederationWorkspaces.and.resolveTo({
      items: [
        { id: 1, name: 'platform', created_at: '2026-01-01', members: 1 },
        { id: 2, name: 'labs', created_at: '2026-01-02', members: 0 },
      ],
      total: 2,
    });
    const fixture = TestBed.createComponent(FederationComponent);
    fixture.detectChanges();
    await fixture.whenStable();

    // two workspaces — nothing auto-selected
    expect(api.getFederationWorkspace).not.toHaveBeenCalled();

    const component = fixture.componentInstance;
    await component.select({ id: 2, name: 'labs', created_at: '2026-01-02', members: 0 });
    expect(api.getFederationWorkspace).toHaveBeenCalledWith(2, jasmine.anything());
  });

  it('surfaces offline state', async () => {
    api.listFederationWorkspaces.and.rejectWith(
      new ApiUnreachableError(new TypeError('down')),
    );
    const fixture = TestBed.createComponent(FederationComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    expect(fixture.componentInstance.phase()).toBe('offline');
  });
});
