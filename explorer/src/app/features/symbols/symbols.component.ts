import { ChangeDetectionStrategy, Component } from '@angular/core';
import { FeaturePlaceholderComponent } from '../../shared/components/feature-placeholder/feature-placeholder.component';

@Component({
  selector: 'atlas-symbols',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FeaturePlaceholderComponent],
  template: `
    <atlas-feature-placeholder
      title="Símbolos"
      description="Detalhe de um símbolo individual: definição, relações e evidência."
    />
  `,
})
export class SymbolsComponent {}
