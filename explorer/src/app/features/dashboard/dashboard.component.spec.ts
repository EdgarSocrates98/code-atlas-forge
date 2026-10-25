import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { ApiService } from '../../core/api/api.service';
import { ApiUnreachableError } from '../../core/api/api.errors';
import { DashboardComponent } from './dashboard.component';

describe('DashboardComponent', () => {
  let api: jasmine.SpyObj<ApiService>;

  beforeEach(async () => {
    api = jasmine.createSpyObj<ApiService>('ApiService', [
      'getHealth',
      'getIndexStatus',
    ]);
    api.getHealth.and.resolveTo({ status: 'ok', service: 'code-atlas-api', version: 1 });
    api.getIndexStatus.and.resolveTo({
      items: [
        {
          repository_id: 1,
          root_handle: '/repo',
          ready_snapshots: 2,
          latest_ready: 4,
          counts: { symbol_occurrences: 200 },
        },
        {
          repository_id: 2,
          root_handle: '/other',
          ready_snapshots: 1,
          latest_ready: 9,
          counts: { symbol_occurrences: 50 },
        },
      ],
    });

    await TestBed.configureTestingModule({
      imports: [DashboardComponent],
      providers: [provideRouter([]), { provide: ApiService, useValue: api }],
    }).compileComponents();
  });

  it('renders aggregate metrics when ready', async () => {
    const fixture = TestBed.createComponent(DashboardComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const component = fixture.componentInstance;
    expect(component.phase()).toBe('ready');
    expect(component.totalReady()).toBe(3);
    expect(component.totalSymbols()).toBe(250);

    const el = fixture.nativeElement as HTMLElement;
    expect(el.textContent).toContain('/repo');
    expect(el.textContent).toContain('/other');
  });

  it('shows the empty hint when no repository is indexed', async () => {
    api.getIndexStatus.and.resolveTo({ items: [] });
    const fixture = TestBed.createComponent(DashboardComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.textContent).toContain('Nenhum repositório indexado');
  });

  it('shows the offline panel when the API is unreachable', async () => {
    api.getHealth.and.rejectWith(new ApiUnreachableError(new TypeError('down')));
    const fixture = TestBed.createComponent(DashboardComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(fixture.componentInstance.phase()).toBe('offline');
    const el = fixture.nativeElement as HTMLElement;
    expect(el.textContent).toContain('API local indisponível');
  });
});
