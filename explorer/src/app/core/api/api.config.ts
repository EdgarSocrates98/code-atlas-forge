import { InjectionToken } from '@angular/core';

/**
 * Base URL for the local Code Atlas HTTP API. The backend is a local-only
 * process; this value is never fetched remotely and never sent telemetry.
 *
 * Em produção o próprio `code-atlas explorer` serve o SPA **e** a API na
 * mesma origem — por isso o default é o caminho relativo `/api/v1`, o que
 * funciona em qualquer porta configurada via `--port` (bug real capturado
 * pelo E2E: URL absoluta fixa quebrava quando a porta não era 8741).
 * Em `ng serve` (dev, porta 4200) aponta para a API local default.
 */
function defaultBase(): string {
  if (typeof document === 'undefined') {
    return 'http://127.0.0.1:8741/api/v1';
  }
  const port = document.location.port;
  if (port === '4200' || port === '4201') {
    return 'http://127.0.0.1:8741/api/v1';
  }
  return '/api/v1';
}

export const ATLAS_API_BASE_URL = new InjectionToken<string>('ATLAS_API_BASE_URL', {
  providedIn: 'root',
  factory: defaultBase,
});
