/**
 * Thin HTTP client that wraps fetch with the base URL from config.
 *
 * The API key is held exclusively in a module-level variable — never in
 * localStorage, sessionStorage, or a cookie. It is injected as the
 * `X-API-Key` header on every protected request. On page reload the
 * variable is empty, and `ApiKeyGate` must re-prompt the user before any
 * protected call is issued.
 */

import { config } from '../config';

// ---------------------------------------------------------------------------
// In-memory API key store
// ---------------------------------------------------------------------------

/** Module-level variable — survives re-renders but not page reloads. */
let _apiKey = '';

/** Store the key after the user enters it in ApiKeyGate. */
export function setApiKey(key: string): void {
  _apiKey = key;
}

/** Read the current in-memory key (empty string when not yet set). */
export function getApiKey(): string {
  return _apiKey;
}

// ---------------------------------------------------------------------------
// HTTP helpers
// ---------------------------------------------------------------------------

type RequestOptions = Omit<RequestInit, 'body'> & {
  body?: unknown;
};

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, headers, ...rest } = options;

  const authHeaders: Record<string, string> = _apiKey
    ? { 'X-API-Key': _apiKey }
    : {};

  const response = await fetch(`${config.apiUrl}${path}`, {
    ...rest,
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders,
      ...headers,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (!response.ok) {
    // Attempt to parse the error body so callers can surface structured
    // messages (field names, missing datasets, Retry-After, etc.).
    let errorBody: unknown = null;
    try {
      errorBody = await response.json();
    } catch {
      // Non-JSON error bodies are fine — errorBody stays null.
    }

    const retryAfter = response.headers.get('Retry-After');

    const error = new Error(
      `API error ${response.status}: ${response.statusText}`,
    ) as Error & { status: number; body: unknown; retryAfter: string | null };
    error.status = response.status;
    error.body = errorBody;
    error.retryAfter = retryAfter;
    throw error;
  }

  // Return undefined for 204 No Content
  if (response.status === 204) {
    return undefined as unknown as T;
  }

  return response.json() as Promise<T>;
}

export const apiClient = {
  get: <T>(path: string, options?: Omit<RequestOptions, 'body'>) =>
    request<T>(path, { ...options, method: 'GET' }),

  post: <T>(path: string, body: unknown, options?: RequestOptions) =>
    request<T>(path, { ...options, method: 'POST', body }),

  put: <T>(path: string, body: unknown, options?: RequestOptions) =>
    request<T>(path, { ...options, method: 'PUT', body }),

  delete: <T>(path: string, options?: Omit<RequestOptions, 'body'>) =>
    request<T>(path, { ...options, method: 'DELETE' }),
};
