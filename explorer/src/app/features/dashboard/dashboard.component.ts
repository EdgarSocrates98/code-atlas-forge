import { ChangeDetectionStrategy, Component, signal } from '@angular/core';
import { ApiService } from '../../core/api/api.service';
import { ApiUnreachableError } from '../../core/api/api.errors';
import { FeaturePlaceholderComponent } from '../../shared/components/feature-placeholder/feature-placeholder.component';

@Component({
  selector: 'atlas-dashboard',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FeaturePlaceholderComponent],
  template: `
    <atlas-feature-placeholder
      title="Painel"
      description="Visão geral do repositório, snapshots e saúde do índice local."
      [loading]="loading()"
      [apiDisconnected]="apiDisconnected()"
    />
  `,
})
export class DashboardComponent {
  readonly loading = signal(false);
  readonly apiDisconnected = signal(false);

  private readonly controller = new AbortController();

  constructor(private readonly api: ApiService) {
    this.load();
  }

  private async load(): Promise<void> {
    this.loading.set(true);
    try {
      await this.api.getRepositories({ signal: this.controller.signal });
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
