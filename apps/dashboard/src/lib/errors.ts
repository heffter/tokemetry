// Map an error into a human-readable message, so pages never render a raw
// stringified exception like "ApiError: request failed: 500".

import { ApiError } from '@/api/client';

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 401 || e.status === 403) {
      return 'Not authorized — check your API token in Settings.';
    }
    if (e.status === 404) return 'Not found.';
    if (e.status >= 500) return 'The server had an error. Try again.';
    return `Request failed (${e.status}).`;
  }
  if (e instanceof TypeError) {
    return 'Cannot reach the server. Is it running?';
  }
  if (e instanceof Error) return e.message;
  return 'Something went wrong.';
}
