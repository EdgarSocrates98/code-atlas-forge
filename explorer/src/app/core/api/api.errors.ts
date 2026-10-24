/**
 * Thrown when the local API cannot be reached at all (connection refused,
 * DNS failure, etc.) — distinct from an HTTP error response.
 */
export class ApiUnreachableError extends Error {
  constructor(public override readonly cause: unknown) {
    super('Code Atlas API está offline ou inacessível em http://127.0.0.1.');
    this.name = 'ApiUnreachableError';
  }
}

/** Thrown when the API responds with a non-2xx HTTP status. */
export class ApiHttpError extends Error {
  constructor(
    public readonly status: number,
    public readonly statusText: string,
    public readonly body: unknown,
  ) {
    super(`Code Atlas API respondeu ${status} ${statusText}`);
    this.name = 'ApiHttpError';
  }
}

/** Thrown when a request is aborted (navigation away, component destroyed). */
export class ApiAbortedError extends Error {
  constructor() {
    super('Requisição cancelada.');
    this.name = 'ApiAbortedError';
  }
}
