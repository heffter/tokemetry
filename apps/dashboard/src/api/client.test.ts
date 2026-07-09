import { describe, expect, it, vi } from 'vitest';
import { ApiClient, ApiError, buildUsageParams } from './client';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('buildUsageParams', () => {
  it('always includes group_by and omits empty filters', () => {
    expect(buildUsageParams({ groupBy: 'model' })).toBe('group_by=model');
  });
  it('includes provided filters', () => {
    const params = buildUsageParams({
      groupBy: 'day',
      from: '2026-07-01',
      machine: 'box-1',
    });
    expect(params).toContain('group_by=day');
    expect(params).toContain('from=2026-07-01');
    expect(params).toContain('machine=box-1');
  });
});

describe('ApiClient', () => {
  it('sends the bearer token and parses JSON', async () => {
    const fetchFn = vi.fn().mockResolvedValue(jsonResponse([{ id: 'box-1' }]));
    const client = new ApiClient(
      'tkm_secret',
      '',
      fetchFn as unknown as typeof fetch
    );

    const machines = await client.machines();

    expect(machines).toEqual([{ id: 'box-1' }]);
    const [, init] = fetchFn.mock.calls[0];
    expect(init.headers.Authorization).toBe('Bearer tkm_secret');
  });

  it('throws ApiError on non-2xx', async () => {
    const fetchFn = vi
      .fn()
      .mockResolvedValue(jsonResponse({ detail: 'no' }, 401));
    const client = new ApiClient('bad', '', fetchFn as unknown as typeof fetch);

    await expect(client.summaryNow()).rejects.toBeInstanceOf(ApiError);
  });

  it('creates a token via POST with a JSON body', async () => {
    const fetchFn = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ label: 'x', token: 'tkm_new', created_at: 'now' }, 201)
      );
    const client = new ApiClient('t', '', fetchFn as unknown as typeof fetch);

    const created = await client.createToken('x');

    expect(created.token).toBe('tkm_new');
    const [url, init] = fetchFn.mock.calls[0];
    expect(url).toBe('/api/v1/tokens');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({ label: 'x' });
  });

  it('returns undefined for 204 responses', async () => {
    const fetchFn = vi
      .fn()
      .mockResolvedValue(new Response(null, { status: 204 }));
    const client = new ApiClient('t', '', fetchFn as unknown as typeof fetch);

    await expect(client.revokeToken('x')).resolves.toBeUndefined();
  });
});
