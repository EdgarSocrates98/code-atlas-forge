import { ApiAbortedError, ApiHttpError, ApiUnreachableError } from '../core/api/api.errors';

/** Broad async-screen state shared by feature components. */
export type FeaturePhase = 'idle' | 'loading' | 'ready' | 'empty' | 'error' | 'offline';

export function isApiOffline(error: unknown): boolean {
  return error instanceof ApiUnreachableError;
}

/**
 * Maps an API error to a short pt-BR message for display.
 * Extracts the typed `{error:{code,message}}` envelope when present so
 * users see the backend's own reason (e.g. `repo-not-indexed`).
 */
export function apiErrorText(error: unknown): string {
  if (error instanceof ApiAbortedError) {
    return 'Requisição cancelada.';
  }
  if (error instanceof ApiUnreachableError) {
    return error.message;
  }
  if (error instanceof ApiHttpError) {
    const detail = httpErrorDetail(error.body);
    const code = httpErrorCode(error.body);
    if (detail) {
      return code
        ? `API respondeu ${error.status} (${code}): ${detail}`
        : `API respondeu ${error.status}: ${detail}`;
    }
    return `API respondeu ${error.status} ${error.statusText}`.trim();
  }
  if (error instanceof Error) {
    return error.message;
  }
  return 'Erro desconhecido.';
}

function httpErrorCode(body: unknown): string {
  const err = asRecord(body)?.['error'];
  const code = asRecord(err)?.['code'];
  return typeof code === 'string' ? code : '';
}

function httpErrorDetail(body: unknown): string {
  const err = asRecord(body)?.['error'];
  const message = asRecord(err)?.['message'];
  return typeof message === 'string' ? message : '';
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null
    ? (value as Record<string, unknown>)
    : null;
}
