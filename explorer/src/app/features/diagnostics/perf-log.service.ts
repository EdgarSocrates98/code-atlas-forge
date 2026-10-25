import { Injectable, signal } from '@angular/core';

export interface PerfEntry {
  /** Logical call label, e.g. 'GET /search'. */
  label: string;
  /** Wall-clock latency in milliseconds. */
  durationMs: number;
  /** epoch ms when the call completed. */
  at: number;
  ok: boolean;
}

const MAX_ENTRIES = 50;

/**
 * In-memory latency log for API calls. Local-only by construction:
 * entries live in a signal, are capped, and are never persisted or sent
 * anywhere. Feature screens call `record()` around their API calls so the
 * diagnostics center can show the latency of the most recent calls.
 */
@Injectable({ providedIn: 'root' })
export class PerfLogService {
  readonly entries = signal<readonly PerfEntry[]>([]);

  /** Record a completed call. Cheap no-throw helper for feature screens. */
  record(label: string, startedAt: number, ok = true): number {
    const durationMs = Math.max(0, Math.round(performance.now() - startedAt));
    this.entries.update((list) =>
      [{ label, durationMs, at: Date.now(), ok }, ...list].slice(0, MAX_ENTRIES),
    );
    return durationMs;
  }

  /** Convenience wrapper: times an async call and records the outcome. */
  async time<T>(label: string, call: () => Promise<T>): Promise<T> {
    const startedAt = performance.now();
    try {
      const result = await call();
      this.record(label, startedAt, true);
      return result;
    } catch (error) {
      this.record(label, startedAt, false);
      throw error;
    }
  }

  clear(): void {
    this.entries.set([]);
  }
}
