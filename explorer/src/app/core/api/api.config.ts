import { InjectionToken } from '@angular/core';

/**
 * Base URL for the local Code Atlas HTTP API. The backend is a local-only
 * process; this value is never fetched remotely and never sent telemetry.
 */
export const ATLAS_API_BASE_URL = new InjectionToken<string>('ATLAS_API_BASE_URL', {
  providedIn: 'root',
  factory: () => 'http://127.0.0.1:8741/api/v1',
});
