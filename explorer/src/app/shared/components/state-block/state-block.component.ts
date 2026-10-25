import { ChangeDetectionStrategy, Component, inject, input } from '@angular/core';
import { ATLAS_API_BASE_URL } from '../../../core/api/api.config';

/**
 * Canonical async state block shared by every feature screen.
 *
 * Renders exactly one of: loading / api-offline / error / empty; otherwise
 * projects the "ready" content. Async regions announce changes through
 * `aria-live="polite"`; error and offline states additionally use
 * `role="alert"` since they require user attention.
 *
 * Usage:
 *   <atlas-state-block state="loading" />
 *   <atlas-state-block state="ready"><!-- content --></atlas-state-block>
 */
export type BlockState = 'loading' | 'offline' | 'error' | 'empty' | 'ready';

@Component({
  selector: 'atlas-state-block',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="state-block" aria-live="polite">
      @switch (state()) {
        @case ('loading') {
          <p class="state-block__panel state-block__panel--loading" role="status">
            <span class="state-block__spinner" aria-hidden="true"></span>
            {{ loadingMessage() }}
          </p>
        }
        @case ('offline') {
          <div class="state-block__panel state-block__panel--error" role="alert">
            <h2 class="state-block__title">API local indisponível</h2>
            <p>
              Não foi possível falar com o Code Atlas em
              <code>{{ apiOrigin() }}</code
              >. Verifique se o processo local está em execução
              (<code>code-atlas explorer</code>) e tente novamente.
            </p>
            @if (detail()) {
              <p class="state-block__detail">{{ detail() }}</p>
            }
          </div>
        }
        @case ('error') {
          <div class="state-block__panel state-block__panel--error" role="alert">
            <h2 class="state-block__title">Falha na operação</h2>
            <p>{{ errorMessage() }}</p>
            @if (detail()) {
              <p class="state-block__detail">{{ detail() }}</p>
            }
          </div>
        }
        @case ('empty') {
          <p class="state-block__panel state-block__panel--empty">{{ emptyMessage() }}</p>
        }
        @case ('ready') {
          <ng-content />
        }
      }
    </div>
  `,
  styles: [
    `
      .state-block__panel {
        padding: var(--atlas-space-3) var(--atlas-space-4);
        border-radius: var(--atlas-radius-md);
        border: 1px solid var(--atlas-color-border);
        background: var(--atlas-color-bg-subtle);
      }

      .state-block__panel--loading {
        display: flex;
        align-items: center;
        gap: var(--atlas-space-2);
        color: var(--atlas-color-text-muted);
      }

      .state-block__spinner {
        width: 0.9rem;
        height: 0.9rem;
        border-radius: 50%;
        border: 2px solid var(--atlas-color-border-strong);
        border-top-color: var(--atlas-color-accent);
        animation: atlas-spin var(--atlas-motion-slow) linear infinite;
        animation-duration: 800ms;
      }

      .state-block__panel--error {
        border-color: var(--atlas-color-danger);
        background: var(--atlas-color-danger-bg);
        color: var(--atlas-color-danger);
      }

      .state-block__panel--empty {
        color: var(--atlas-color-text-muted);
      }

      .state-block__title {
        margin: 0 0 var(--atlas-space-2);
        font-size: var(--atlas-font-size-md);
      }

      .state-block__detail {
        font-size: var(--atlas-font-size-sm);
        color: inherit;
        opacity: 0.85;
        margin: var(--atlas-space-2) 0 0;
        overflow-wrap: anywhere;
      }

      @keyframes atlas-spin {
        to {
          transform: rotate(360deg);
        }
      }

      @media (prefers-reduced-motion: reduce) {
        .state-block__spinner {
          animation: none;
        }
      }
    `,
  ],
})
export class StateBlockComponent {
  readonly state = input.required<BlockState>();
  readonly loadingMessage = input('Carregando…');
  readonly emptyMessage = input('Nada para exibir.');
  readonly errorMessage = input('Ocorreu um erro inesperado.');
  /** Optional technical detail shown under the main message. */
  readonly detail = input('');

  private readonly apiBase = inject(ATLAS_API_BASE_URL, { optional: true }) ?? '/api/v1';

  /**
   * The address the app actually talks to — never a hardcoded port. In
   * production the API is same-origin (any `--port`), in `ng serve` dev it
   * is the absolute local default.
   */
  apiOrigin(): string {
    const base = this.apiBase;
    if (base.startsWith('http')) {
      try {
        return new URL(base).origin;
      } catch {
        return base;
      }
    }
    if (typeof document !== 'undefined' && document.location?.origin) {
      return document.location.origin;
    }
    return 'o processo local';
  }
}
