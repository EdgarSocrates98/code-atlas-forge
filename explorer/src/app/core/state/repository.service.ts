import { Injectable, signal } from '@angular/core';
import type { Repository } from '../models/api.models';

/**
 * Holds the currently selected repository context shared across feature
 * modules. Deliberately minimal — repository discovery/selection UI is a
 * future increment; this is the placeholder seam the toolbar binds to.
 */
@Injectable({ providedIn: 'root' })
export class RepositoryContextService {
  readonly repositories = signal<Repository[]>([]);
  readonly selected = signal<Repository | null>(null);

  setRepositories(repositories: Repository[]): void {
    this.repositories.set(repositories);
    if (!this.selected() && repositories.length > 0) {
      this.selected.set(repositories[0]);
    }
  }

  select(repository: Repository | null): void {
    this.selected.set(repository);
  }
}
