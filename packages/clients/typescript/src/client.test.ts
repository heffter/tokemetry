import { describe, expect, it, vi } from 'vitest';
import {
  IngestAuthError,
  IngestClient,
  IngestRetryError,
  type UsageEventV2,
} from './client';

function event(id: string): UsageEventV2 {
  return {
    schema_version: 2,
    event_id: id,
    event_kind: 'attempt',
    finality: 'final',
    sequence: 0,
    provider: 'anthropic',
    native_model: 'claude-opus-4-5',
    ts_started: '2026-07-10T12:00:00Z',
    source: { type: 'gateway', name: 'gw-1', version: '1.0' },
  } as UsageEventV2;
}

function client(
  fetchFn: typeof fetch,
  overrides: Partial<ConstructorParameters<typeof IngestClient>[0]> = {}
): IngestClient {
  return new IngestClient({
    serverUrl: 'http://server',
    token: 't',
    fetchFn,
    sleepFn: async () => {},
    randomFn: () => 0.5,
    ...overrides,
  });
}

function ok(accepted: number): Response {
  return new Response(JSON.stringify({ accepted }), { status: 200 });
}

describe('IngestClient batching', () => {
  it('splits events into batches of batchSize', async () => {
    const fetchFn = vi.fn().mockResolvedValue(ok(2));
    const result = await client(fetchFn as unknown as typeof fetch, {
      batchSize: 2,
    }).ingest([event('a'), event('b'), event('c')]);
    expect(fetchFn).toHaveBeenCalledTimes(2); // [a,b] then [c]
    expect(result.batches).toBe(2);
  });

  it('sends the batch envelope with the bearer token', async () => {
    const fetchFn = vi.fn().mockResolvedValue(ok(1));
    await client(fetchFn as unknown as typeof fetch).ingest([event('a')]);
    const [url, init] = fetchFn.mock.calls[0]!;
    expect(url).toBe('http://server/api/v2/ingest/events');
    expect(init.headers.Authorization).toBe('Bearer t');
    const body = JSON.parse(init.body);
    expect(body.schema_version).toBe(2);
    expect(body.events).toHaveLength(1);
  });
});

describe('IngestClient retry', () => {
  it('retries on 429 then succeeds', async () => {
    const fetchFn = vi
      .fn()
      .mockResolvedValueOnce(new Response('', { status: 429 }))
      .mockResolvedValueOnce(ok(1));
    const result = await client(fetchFn as unknown as typeof fetch).ingest([
      event('a'),
    ]);
    expect(fetchFn).toHaveBeenCalledTimes(2);
    expect(result.accepted).toBe(1);
  });

  it('retries on 500 then succeeds', async () => {
    const fetchFn = vi
      .fn()
      .mockResolvedValueOnce(new Response('', { status: 503 }))
      .mockResolvedValueOnce(ok(1));
    await client(fetchFn as unknown as typeof fetch).ingest([event('a')]);
    expect(fetchFn).toHaveBeenCalledTimes(2);
  });

  it('throws IngestRetryError after exhausting retries', async () => {
    const fetchFn = vi.fn().mockResolvedValue(new Response('', { status: 500 }));
    await expect(
      client(fetchFn as unknown as typeof fetch, { maxRetries: 2 }).ingest([
        event('a'),
      ])
    ).rejects.toBeInstanceOf(IngestRetryError);
    expect(fetchFn).toHaveBeenCalledTimes(3); // initial + 2 retries
  });
});

describe('IngestClient auth', () => {
  it('pauses (IngestAuthError) on 401 without retrying', async () => {
    const fetchFn = vi.fn().mockResolvedValue(new Response('', { status: 401 }));
    await expect(
      client(fetchFn as unknown as typeof fetch).ingest([event('a')])
    ).rejects.toBeInstanceOf(IngestAuthError);
    expect(fetchFn).toHaveBeenCalledTimes(1); // no retry on 401
  });
});

describe('IngestClient poison isolation', () => {
  it('isolates a single poison event on 422', async () => {
    const fetchFn = vi.fn().mockResolvedValue(new Response('', { status: 422 }));
    const result = await client(fetchFn as unknown as typeof fetch).ingest([
      event('bad'),
    ]);
    expect(result.rejected).toBe(1);
    expect(result.poisonEvents.map((e) => e.event_id)).toEqual(['bad']);
  });

  it('bisects a rejected batch to isolate the poison, accepting the rest', async () => {
    // A 3-event batch 400s; the middle event is poison. Bisection accepts the
    // two good ones and isolates only the bad one.
    const fetchFn = vi.fn(async (_url: string, init: { body: string }) => {
      const ids: string[] = JSON.parse(init.body).events.map(
        (e: UsageEventV2) => e.event_id
      );
      if (ids.includes('bad')) return new Response('', { status: 400 });
      return ok(ids.length);
    });
    const result = await client(fetchFn as unknown as typeof fetch).ingest([
      event('a'),
      event('bad'),
      event('c'),
    ]);
    expect(result.rejected).toBe(1);
    expect(result.poisonEvents.map((e) => e.event_id)).toEqual(['bad']);
    expect(result.accepted).toBe(2);
  });
});
