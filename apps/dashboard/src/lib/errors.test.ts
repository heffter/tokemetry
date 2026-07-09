import { describe, expect, it } from 'vitest';
import { ApiError } from '@/api/client';
import { errorMessage } from './errors';

describe('errorMessage', () => {
  it('maps auth failures to a token hint', () => {
    expect(errorMessage(new ApiError(401, 'x'))).toContain('token');
    expect(errorMessage(new ApiError(403, 'x'))).toContain('token');
  });
  it('maps server errors', () => {
    expect(errorMessage(new ApiError(500, 'x'))).toContain(
      'server had an error'
    );
  });
  it('maps other statuses with the code', () => {
    expect(errorMessage(new ApiError(422, 'x'))).toContain('422');
  });
  it('maps network TypeErrors', () => {
    expect(errorMessage(new TypeError('failed to fetch'))).toContain(
      'Cannot reach'
    );
  });
  it('passes through generic error messages', () => {
    expect(errorMessage(new Error('boom'))).toBe('boom');
  });
  it('handles non-errors', () => {
    expect(errorMessage('nope')).toBe('Something went wrong.');
  });
});
