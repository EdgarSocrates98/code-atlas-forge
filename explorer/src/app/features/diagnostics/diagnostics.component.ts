import { ChangeDetectionStrategy, Component } from '@angular/core';
import { GlobalErrorHandler } from '../../core/errors/global-error-handler';
import { FeaturePlaceholderComponent } from '../../shared/components/feature-placeholder/feature-placeholder.component';

@Component({
  selector: 'atlas-diagnostics',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FeaturePlaceholderComponent],
  template: `
    @if (errorHandler.lastError(); as last) {
      <section class="diagnostics-banner" role="alert">
        <h2>Último erro capturado</h2>
        <p>{{ last.message }}</p>
      </section>
    }
    <atlas-feature-placeholder
      title="Diagnósticos"
      description="Avisos de resolução, erros de indexação e saúde geral do workbench."
    />
  `,
  styles: [
    `
      .diagnostics-banner {
        margin: var(--atlas-space-5) var(--atlas-space-5) 0;
        padding: var(--atlas-space-3) var(--atlas-space-4);
        border: 1px solid var(--atlas-color-danger);
        background: var(--atlas-color-danger-bg);
        color: var(--atlas-color-danger);
        border-radius: var(--atlas-radius-md);
      }
    `,
  ],
})
export class DiagnosticsComponent {
  constructor(readonly errorHandler: GlobalErrorHandler) {}
}
