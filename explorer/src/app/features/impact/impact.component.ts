import { ChangeDetectionStrategy, Component, OnDestroy, signal } from '@angular/core';
import { FeaturePlaceholderComponent } from '../../shared/components/feature-placeholder/feature-placeholder.component';

@Component({
  selector: 'atlas-impact',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FeaturePlaceholderComponent],
  template: `
    <atlas-feature-placeholder
      title="Impacto"
      description="Análise de impacto de uma revisão: símbolos alterados, propagação e candidatos de teste."
      [loading]="loading()"
      [apiDisconnected]="apiDisconnected()"
    />
  `,
})
export class ImpactComponent implements OnDestroy {
  readonly loading = signal(false);
  readonly apiDisconnected = signal(false);

  ngOnDestroy(): void {
    // No in-flight requests until a revision is selected (future increment).
  }
}
