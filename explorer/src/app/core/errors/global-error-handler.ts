import { ErrorHandler, Injectable, NgZone, signal } from '@angular/core';
import { ApiAbortedError } from '../api/api.errors';

export interface CaughtError {
  message: string;
  occurredAt: number;
}

/**
 * Process-wide error boundary. Nothing here calls any remote endpoint —
 * errors are kept in-memory (signal) for the diagnostics feature to surface
 * and are logged to the console in non-production builds only.
 */
@Injectable({ providedIn: 'root' })
export class GlobalErrorHandler implements ErrorHandler {
  readonly lastError = signal<CaughtError | null>(null);

  constructor(private readonly zone: NgZone) {}

  handleError(error: unknown): void {
    // Aborted requests are expected (navigation, component teardown) — not errors.
    if (error instanceof ApiAbortedError) {
      return;
    }

    const message = error instanceof Error ? error.message : 'Erro desconhecido.';

    this.zone.run(() => {
      this.lastError.set({ message, occurredAt: Date.now() });
    });

    if (!this.isProduction()) {
      // eslint-disable-next-line no-console
      console.error('[Code Atlas Explorer]', error);
    }
  }

  private isProduction(): boolean {
    return typeof ngDevMode !== 'undefined' && ngDevMode === false;
  }
}

declare const ngDevMode: boolean | undefined;
