import { ChangeDetectionStrategy, Component, Input } from '@angular/core';

/**
 * Shared presentational shell for feature stubs: renders loading / error /
 * empty states consistently so every feature route looks and behaves the
 * same before real UI lands on top of it.
 */
@Component({
  selector: 'atlas-feature-placeholder',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="placeholder" aria-live="polite">
      <header class="placeholder__header">
        <h1>{{ title }}</h1>
        <p class="placeholder__description">{{ description }}</p>
      </header>

      @if (loading) {
        <p class="placeholder__state placeholder__state--loading">Carregando…</p>
      } @else if (apiDisconnected) {
        <p class="placeholder__state placeholder__state--error" role="alert">
          API local indisponível em http://127.0.0.1. Verifique se o processo do Code Atlas está em
          execução.
        </p>
      } @else {
        <p class="placeholder__state placeholder__state--empty">
          Nenhum dado ainda. Esta tela será preenchida em um ciclo futuro.
        </p>
      }
    </section>
  `,
  styles: [
    `
      .placeholder {
        padding: var(--atlas-space-5);
        max-width: 72ch;
      }

      .placeholder__header h1 {
        font-size: var(--atlas-font-size-xl);
        margin: 0 0 var(--atlas-space-2);
      }

      .placeholder__description {
        color: var(--atlas-color-text-muted);
        margin: 0 0 var(--atlas-space-4);
      }

      .placeholder__state {
        padding: var(--atlas-space-3) var(--atlas-space-4);
        border-radius: var(--atlas-radius-md);
        border: 1px solid var(--atlas-color-border);
        background: var(--atlas-color-bg-subtle);
      }

      .placeholder__state--error {
        border-color: var(--atlas-color-danger);
        background: var(--atlas-color-danger-bg);
        color: var(--atlas-color-danger);
      }
    `,
  ],
})
export class FeaturePlaceholderComponent {
  @Input({ required: true }) title = '';
  @Input() description = '';
  @Input() loading = false;
  @Input() apiDisconnected = false;
}
