import { describe, expect, it, vi } from 'vitest';
import {
  ApiClient,
  ApiError,
  appendV2Filters,
  appendV2Page,
  buildUsageParams,
  buildV2GroupedParams,
} from './client';

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

describe('appendV2Filters', () => {
  it('omits unset filters and maps model/session to their query aliases', () => {
    const params = new URLSearchParams();
    appendV2Filters(params, {
      provider: 'anthropic',
      model: 'claude-opus-4-5',
    });
    expect(params.get('provider')).toBe('anthropic');
    expect(params.get('model')).toBe('claude-opus-4-5');
    expect(params.get('source')).toBeNull();
    expect(params.get('session')).toBeNull();
  });

  it('serializes the unknown_* pseudo-filters as boolean flags', () => {
    const params = new URLSearchParams();
    appendV2Filters(params, { unknownProvider: true, unknownModel: false });
    expect(params.get('unknown_provider')).toBe('true');
    // A false flag is omitted rather than sent as unknown_model=false.
    expect(params.get('unknown_model')).toBeNull();
  });
});

describe('appendV2Page', () => {
  it('includes limit and cursor only when set', () => {
    const params = new URLSearchParams();
    appendV2Page(params, { limit: 25, cursor: 'abc' });
    expect(params.get('limit')).toBe('25');
    expect(params.get('cursor')).toBe('abc');
    const empty = new URLSearchParams();
    appendV2Page(empty, {});
    expect([...empty.keys()]).toEqual([]);
  });
});

describe('buildV2GroupedParams', () => {
  it('always includes the range and threads group_by, sort, and filters', () => {
    const query = buildV2GroupedParams({
      from: '2026-01-01T00:00:00Z',
      to: '2026-02-01T00:00:00Z',
      groupBy: 'provider',
      sort: '-total_tokens',
      provider: 'openai',
    });
    const params = new URLSearchParams(query);
    expect(params.get('from')).toBe('2026-01-01T00:00:00Z');
    expect(params.get('to')).toBe('2026-02-01T00:00:00Z');
    expect(params.get('group_by')).toBe('provider');
    expect(params.get('sort')).toBe('-total_tokens');
    expect(params.get('provider')).toBe('openai');
  });
});

describe('ApiClient v2 read endpoints', () => {
  it('fetches the provider registry', async () => {
    const fetchFn = vi
      .fn()
      .mockResolvedValue(jsonResponse([{ id: 'anthropic' }]));
    const client = new ApiClient('t', '', fetchFn as unknown as typeof fetch);

    await client.v2Providers();

    const [url] = fetchFn.mock.calls[0];
    expect(url).toBe('/api/v2/providers');
  });

  it('shapes the v2 usage request from range, group_by, and filters', async () => {
    const fetchFn = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ group_by: 'model', rows: [], warnings: [] })
      );
    const client = new ApiClient('t', '', fetchFn as unknown as typeof fetch);

    await client.v2Usage({
      from: '2026-01-01T00:00:00Z',
      to: '2026-02-01T00:00:00Z',
      groupBy: 'model',
      provider: 'anthropic',
    });

    const [url] = fetchFn.mock.calls[0];
    expect(url).toMatch(/^\/api\/v2\/usage\?/);
    const params = new URLSearchParams(url.split('?')[1]);
    expect(params.get('group_by')).toBe('model');
    expect(params.get('provider')).toBe('anthropic');
  });

  it('threads pagination and logical_request_id into an attempts request', async () => {
    const fetchFn = vi
      .fn()
      .mockResolvedValue(jsonResponse({ attempts: [], next_cursor: null }));
    const client = new ApiClient('t', '', fetchFn as unknown as typeof fetch);

    await client.v2Attempts({
      from: '2026-01-01T00:00:00Z',
      to: '2026-02-01T00:00:00Z',
      limit: 10,
      cursor: 'c1',
      logicalRequestId: 'lr_1',
    });

    const [url] = fetchFn.mock.calls[0];
    const params = new URLSearchParams(url.split('?')[1]);
    expect(params.get('limit')).toBe('10');
    expect(params.get('cursor')).toBe('c1');
    expect(params.get('logical_request_id')).toBe('lr_1');
  });
});
