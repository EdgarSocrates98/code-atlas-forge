import { fakeAsync, TestBed, tick } from '@angular/core/testing';
import { ThemeService } from './theme.service';

describe('ThemeService', () => {
  beforeEach(() => {
    localStorage.removeItem('atlas-theme');
    document.documentElement.removeAttribute('data-theme');
    TestBed.configureTestingModule({});
  });

  it('defaults to system preference when nothing is stored', () => {
    const service = TestBed.inject(ThemeService);
    expect(service.preference()).toBe('system');
  });

  it('persists explicit preference to localStorage', () => {
    const service = TestBed.inject(ThemeService);
    service.setPreference('dark');
    expect(localStorage.getItem('atlas-theme')).toBe('dark');
    expect(service.resolvedTheme()).toBe('dark');
  });

  it('applies data-theme attribute to <html> for explicit preference', fakeAsync(() => {
    const service = TestBed.inject(ThemeService);
    service.setPreference('light');
    tick();
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
  }));

  it('removes data-theme attribute when preference is system', fakeAsync(() => {
    const service = TestBed.inject(ThemeService);
    service.setPreference('dark');
    tick();
    service.setPreference('system');
    tick();
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
  }));

  it('toggle() flips between light and dark', () => {
    const service = TestBed.inject(ThemeService);
    service.setPreference('light');
    service.toggle();
    expect(service.resolvedTheme()).toBe('dark');
    service.toggle();
    expect(service.resolvedTheme()).toBe('light');
  });
});
