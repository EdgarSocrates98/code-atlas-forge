import { ChangeDetectionStrategy, Component, signal } from '@angular/core';
import { FeaturePlaceholderComponent } from '../../shared/components/feature-placeholder/feature-placeholder.component';

@Component({
  selector: 'atlas-context',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FeaturePlaceholderComponent],
  template: `
    <atlas-feature-placeholder
      title="Context Packs"
      description="Pacotes de contexto gerados para símbolos, prontos para consumo por agentes."
      [loading]="loading()"
      [apiDisconnected]="apiDisconnected()"
    />
  `,
})
export class ContextComponent {
  readonly loading = signal(false);
  readonly apiDisconnected = signal(false);
}
