import { Injectable, computed, effect, signal } from '@angular/core';

export type ThemePreference = 'light' | 'dark' | 'system';
export type ResolvedTheme = 'light' | 'dark';

const STORAGE_KEY = 'atlas-theme';

/**
 * Owns theme preference state (signals) and persists the user's explicit
 * choice to localStorage. When preference is 'system', the resolved theme
 * tracks prefers-color-scheme live.
 */
@Injectable({ providedIn: 'root' })
export class ThemeService {
  private readonly mediaQuery = this.matchMediaDark();

  readonly preference = signal<ThemePreference>(this.readStoredPreference());
  readonly systemPrefersDark = signal<boolean>(this.mediaQuery?.matches ?? false);

  readonly resolvedTheme = computed<ResolvedTheme>(() =>
    this.computeResolved(this.preference(), this.systemPrefersDark()));

  constructor() {
    this.mediaQuery?.addEventListener('change', (event) => {
      this.systemPrefersDark.set(event.matches);
    });

    effect(() => {
      this.applyToDocument(this.preference(), this.resolvedTheme());
    });
  }

  setPreference(preference: ThemePreference): void {
    this.preference.set(preference);
    this.persist(preference);
  }

  toggle(): void {
    const current = this.resolvedTheme();
    this.setPreference(current === 'dark' ? 'light' : 'dark');
  }

  private computeResolved(preference: ThemePreference, systemDark: boolean): ResolvedTheme {
    if (preference === 'system') {
      return systemDark ? 'dark' : 'light';
    }
    return preference;
  }

  private applyToDocument(preference: ThemePreference, resolved: ResolvedTheme): void {
    if (typeof document === 'undefined') {
      return;
    }
    if (preference === 'system') {
      document.documentElement.removeAttribute('data-theme');
      return;
    }
    document.documentElement.setAttribute('data-theme', resolved);
  }

  private readStoredPreference(): ThemePreference {
    if (typeof localStorage === 'undefined') {
      return 'system';
    }
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'light' || stored === 'dark' || stored === 'system') {
      return stored;
    }
    return 'system';
  }

  private persist(preference: ThemePreference): void {
    if (typeof localStorage === 'undefined') {
      return;
    }
    try {
      localStorage.setItem(STORAGE_KEY, preference);
    } catch {
      // Storage unavailable (private mode, quota) — preference stays in-memory only.
    }
  }

  private matchMediaDark(): MediaQueryList | null {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return null;
    }
    return window.matchMedia('(prefers-color-scheme: dark)');
  }
}
