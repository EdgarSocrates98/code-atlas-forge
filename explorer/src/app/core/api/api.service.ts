import { Inject, Injectable } from '@angular/core';
import { ATLAS_API_BASE_URL } from './api.config';
import { ApiAbortedError, ApiHttpError, ApiUnreachableError } from './api.errors';
import type {
  ContextPack,
  ImpactResult,
  RelationEdge,
  Repository,
  SearchResult,
  Snapshot,
  SymbolNode,
} from '../models/api.models';

export interface ApiRequestOptions {
  signal?: AbortSignal;
}

/**
 * Thin fetch-based client for the local Code Atlas HTTP API.
 *
 * Design decisions:
 * - fetch() over HttpClient: the API is strictly local and loopback-only,
 *   and fetch keeps the dependency surface (and bundle) minimal while still
 *   giving us first-class AbortController support per call.
 * - Every call accepts an external AbortSignal AND creates its own internal
 *   AbortController so callers never need to manage cleanup manually; call
 *   `cancelAll()` on component destroy to abort any in-flight calls made
 *   without an explicit signal.
 */
@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly inFlight = new Set<AbortController>();

  constructor(@Inject(ATLAS_API_BASE_URL) private readonly baseUrl: string) {}

  getRepositories(options?: ApiRequestOptions): Promise<Repository[]> {
    return this.request<Repository[]>('GET', '/repositories', undefined, options);
  }

  getSnapshot(repositoryId: string, options?: ApiRequestOptions): Promise<Snapshot> {
    return this.request<Snapshot>(
      'GET',
      `/repositories/${encodeURIComponent(repositoryId)}/snapshot`,
      undefined,
      options,
    );
  }

  search(query: string, options?: ApiRequestOptions): Promise<SearchResult> {
    const params = new URLSearchParams({ q: query });
    return this.request<SearchResult>('GET', `/search?${params.toString()}`, undefined, options);
  }

  getSymbol(symbolId: string, options?: ApiRequestOptions): Promise<SymbolNode> {
    return this.request<SymbolNode>(
      'GET',
      `/symbols/${encodeURIComponent(symbolId)}`,
      undefined,
      options,
    );
  }

  getRelations(symbolId: string, options?: ApiRequestOptions): Promise<RelationEdge[]> {
    return this.request<RelationEdge[]>(
      'GET',
      `/symbols/${encodeURIComponent(symbolId)}/relations`,
      undefined,
      options,
    );
  }

  getImpact(revisionId: string, options?: ApiRequestOptions): Promise<ImpactResult> {
    return this.request<ImpactResult>(
      'GET',
      `/revisions/${encodeURIComponent(revisionId)}/impact`,
      undefined,
      options,
    );
  }

  getContextPack(
    symbolId: string,
    options?: ApiRequestOptions,
  ): Promise<ContextPack> {
    return this.request<ContextPack>(
      'GET',
      `/symbols/${encodeURIComponent(symbolId)}/context-pack`,
      undefined,
      options,
    );
  }

  /** Aborts every request currently in flight that this service started. */
  cancelAll(): void {
    for (const controller of this.inFlight) {
      controller.abort();
    }
    this.inFlight.clear();
  }

  private async request<T>(
    method: 'GET' | 'POST' | 'PUT' | 'DELETE',
    path: string,
    body: unknown,
    options?: ApiRequestOptions,
  ): Promise<T> {
    const controller = new AbortController();
    this.inFlight.add(controller);

    if (options?.signal) {
      if (options.signal.aborted) {
        controller.abort();
      } else {
        options.signal.addEventListener('abort', () => controller.abort(), { once: true });
      }
    }

    try {
      const response = await fetch(`${this.baseUrl}${path}`, {
        method,
        signal: controller.signal,
        headers: body ? { 'Content-Type': 'application/json' } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });

      if (!response.ok) {
        let parsedBody: unknown = null;
        try {
          parsedBody = await response.json();
        } catch {
          parsedBody = null;
        }
        throw new ApiHttpError(response.status, response.statusText, parsedBody);
      }

      if (response.status === 204) {
        return undefined as T;
      }

      return (await response.json()) as T;
    } catch (error) {
      if (error instanceof ApiHttpError) {
        throw error;
      }
      if (error instanceof DOMException && error.name === 'AbortError') {
        throw new ApiAbortedError();
      }
      throw new ApiUnreachableError(error);
    } finally {
      this.inFlight.delete(controller);
    }
  }
}
