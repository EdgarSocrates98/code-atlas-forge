import { ChangeDetectionStrategy, Component, OnDestroy, signal } from '@angular/core';
import { ApiService } from '../../core/api/api.service';
import { ApiUnreachableError } from '../../core/api/api.errors';
import { FeaturePlaceholderComponent } from '../../shared/components/feature-placeholder/feature-placeholder.component';

@Component({
  selector: 'atlas-search',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FeaturePlaceholderComponent],
  template: `
    <atlas-feature-placeholder
      title="Busca"
      description="Busca híbrida (léxica + semântica) sobre o grafo de símbolos."
      [loading]="loading()"
      [apiDisconnected]="apiDisconnected()"
    />
  `,
})
export class SearchComponent implements OnDestroy {
  readonly loading = signal(false);
  readonly apiDisconnected = signal(false);
  private readonly controller = new AbortController();

  constructor(private readonly api: ApiService) {
    this.probe();
  }

  private async probe(): Promise<void> {
    this.loading.set(true);
    try {
      await this.api.search('', { signal: this.controller.signal });
    } catch (error) {
      if (error instanceof ApiUnreachableError) {
        this.apiDisconnected.set(true);
      }
    } finally {
      this.loading.set(false);
    }
  }

  ngOnDestroy(): void {
    this.controller.abort();
  }
}
