import { ChangeDetectionStrategy, Component, input } from '@angular/core';

export interface DiagnosticItem {
  code: string;
  message: string;
  severity?: string;
}

/**
 * Typed diagnostics block — renders backend diagnostics (code + message)
 * with a warning affordance. Diagnostics are informational evidence, not
 * success states: the panel is always styled as a warning.
 */
@Component({
  selector: 'atlas-diagnostics-list',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="diagnostics" [attr.aria-label]="title()">
      <h2 class="diagnostics__title">{{ title() }}</h2>
      <ul class="diagnostics__list">
        @for (diag of items(); track $index) {
          <li class="diagnostics__item">
            <code class="diagnostics__code">{{ diag.code }}</code>
            @if (diag.severity) {
              <span class="diagnostics__severity">[{{ diag.severity }}]</span>
            }
            <span class="diagnostics__message">{{ diag.message }}</span>
          </li>
        }
      </ul>
    </section>
  `,
  styles: [
    `
      .diagnostics {
        border: 1px solid var(--atlas-color-warning);
        border-radius: var(--atlas-radius-md);
        background: var(--atlas-color-warning-bg);
        padding: var(--atlas-space-3) var(--atlas-space-4);
      }

      .diagnostics__title {
        margin: 0 0 var(--atlas-space-2);
        font-size: var(--atlas-font-size-sm);
        color: var(--atlas-color-warning);
      }

      .diagnostics__list {
        margin: 0;
        padding: 0;
        list-style: none;
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-1);
      }

      .diagnostics__item {
        font-size: var(--atlas-font-size-sm);
        overflow-wrap: anywhere;
      }

      .diagnostics__code {
        font-family: var(--atlas-font-family-mono);
        font-weight: var(--atlas-font-weight-bold);
        margin-right: var(--atlas-space-2);
      }

      .diagnostics__severity {
        color: var(--atlas-color-text-muted);
        margin-right: var(--atlas-space-1);
      }
    `,
  ],
})
export class DiagnosticsListComponent {
  readonly items = input.required<readonly DiagnosticItem[]>();
  readonly title = input('Diagnósticos');
}
