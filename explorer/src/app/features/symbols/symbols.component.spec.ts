import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { ApiService, type ApiSymbolDetail } from '../../core/api/api.service';
import { ApiHttpError, ApiUnreachableError } from '../../core/api/api.errors';
import { SymbolsComponent } from './symbols.component';

const DETAIL: ApiSymbolDetail = {
  id: 'occ:9',
  occurrence_id: 9,
  symbol_id: 90,
  qname: 'pkg.mod.target',
  kind: 'method',
  language: 'python',
  path: 'pkg/mod.py',
  span: { start: 30, end: 44 },
  parse_status: 'ok',
  relations_summary: [
    { type: 'calls', resolution_status: 'supported', n: 3 },
    { type: 'defines', resolution_status: 'unresolved', n: 1 },
  ],
};

function setup(paramId: string, api: Partial<ApiService>) {
  TestBed.configureTestingModule({
    imports: [SymbolsComponent],
    providers: [
      provideRouter([]),
      { provide: ApiService, useValue: api },
      {
        provide: ActivatedRoute,
        useValue: {
          snapshot: { paramMap: convertToParamMap({ id: paramId }) },
          paramMap: of(convertToParamMap({ id: paramId })),
        },
      },
    ],
  });
  const fixture = TestBed.createComponent(SymbolsComponent);
  fixture.detectChanges();
  return fixture;
}

describe('SymbolsComponent', () => {
  let api: { getSymbolDetail: jasmine.Spy };

  beforeEach(() => {
    api = { getSymbolDetail: jasmine.createSpy('getSymbolDetail').and.resolveTo(DETAIL) };
  });

  it('loads the detail for a numeric occurrence id', async () => {
    const fixture = setup('9', api as unknown as ApiService);
    await fixture.whenStable();
    fixture.detectChanges();

    expect(api.getSymbolDetail).toHaveBeenCalledWith(9, {}, jasmine.any(Object));
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('target');
    expect(text).toContain('pkg.mod.target');
    expect(text).toContain('method');
    expect(text).toContain('pkg/mod.py');
    expect(text).toContain('30 – 44');
    expect(text).toContain('calls');
    expect(text).toContain('Apoiado');
    expect(text).toContain('Não resolvido');
  });

  it('accepts occ:{id} route params', async () => {
    const fixture = setup('occ:9', api as unknown as ApiService);
    await fixture.whenStable();
    expect(api.getSymbolDetail).toHaveBeenCalledWith(9, {}, jasmine.any(Object));
  });

  it('shows the empty state for a malformed id', () => {
    const fixture = setup('not-a-number', api as unknown as ApiService);
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Identificador de símbolo inválido');
    expect(api.getSymbolDetail).not.toHaveBeenCalled();
  });

  it('maps HTTP errors to the error state', async () => {
    api.getSymbolDetail.and.rejectWith(new ApiHttpError(404, 'Not Found', null));
    const fixture = setup('9', api as unknown as ApiService);
    await fixture.whenStable();
    fixture.detectChanges();
    expect(fixture.componentInstance.phase()).toBe('error');
  });

  it('maps unreachable API to the offline state', async () => {
    api.getSymbolDetail.and.rejectWith(new ApiUnreachableError(new Error('down')));
    const fixture = setup('9', api as unknown as ApiService);
    await fixture.whenStable();
    fixture.detectChanges();
    expect(fixture.componentInstance.phase()).toBe('offline');
  });
});
