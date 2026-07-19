import { describe, expect, it } from 'vitest';
import {
  attemptSummary,
  failureRateBy,
  latencyValues,
  orderAttempts,
  percentile,
} from './trace';
import type { AttemptV2 } from '@/api/types-v2';

function attempt(overrides: Partial<AttemptV2> = {}): AttemptV2 {
  return {
    event_id: 'a1',
    provider: 'anthropic',
    native_model: 'claude-opus-4-5',
    requested_model: 'claude-opus-4-5',
    routed_model: 'claude-opus-4-5',
    ts_started: '2026-07-10T10:00:00Z',
    ts_completed: '2026-07-10T10:00:01Z',
    latency_ms: 1000,
    success: true,
    logical_request_id: 'lr1',
    session_id: 's1',
    source: 'collector',
    input_tokens: 100,
    output_tokens: 50,
    cache_read_tokens: 0,
    cache_write_short_tokens: 0,
    cache_write_long_tokens: 0,
    reasoning_tokens: 0,
    cost_usd: null,
    ...overrides,
  };
}

describe('orderAttempts', () => {
  it('orders a fallback chain by start time even when input is out of order', () => {
    const chain = orderAttempts([
      attempt({ event_id: 'b', ts_started: '2026-07-10T10:00:05Z' }),
      attempt({ event_id: 'a', ts_started: '2026-07-10T10:00:00Z' }),
    ]);
    expect(chain.map((a) => a.event_id)).toEqual(['a', 'b']);
  });

  it('keeps input order for equal timestamps (stable tie-break)', () => {
    const chain = orderAttempts([
      attempt({ event_id: 'x', ts_started: '2026-07-10T10:00:00Z' }),
      attempt({ event_id: 'z', ts_started: '2026-07-10T10:00:00Z' }),
    ]);
    expect(chain.map((a) => a.event_id)).toEqual(['x', 'z']);
  });

  it('does not mutate the input array', () => {
    const input = [attempt({ event_id: 'a' })];
    orderAttempts(input);
    expect(input[0].event_id).toBe('a');
  });
});

describe('failureRateBy', () => {
  it('computes failure rate per provider, sorted worst first', () => {
    const stats = failureRateBy(
      [
        attempt({ provider: 'anthropic', success: true }),
        attempt({ provider: 'anthropic', success: false }),
        attempt({ provider: 'openai', success: false }),
      ],
      (a) => a.provider
    );
    const byKey = Object.fromEntries(stats.map((s) => [s.key, s]));
    expect(byKey.anthropic.rate).toBeCloseTo(0.5);
    expect(byKey.openai.rate).toBe(1);
    // Worst rate first.
    expect(stats[0].key).toBe('openai');
  });
});

describe('attemptSummary', () => {
  it('counts fallbacks as extra attempts within a logical request', () => {
    const s = attemptSummary([
      attempt({ event_id: '1', logical_request_id: 'r1', success: false }),
      attempt({ event_id: '2', logical_request_id: 'r1', success: true }),
      attempt({ event_id: '3', logical_request_id: 'r2', success: true }),
    ]);
    expect(s.attempts).toBe(3);
    expect(s.successes).toBe(2);
    expect(s.failures).toBe(1);
    expect(s.logicalRequests).toBe(2); // r1, r2
    expect(s.fallbacks).toBe(1); // r1 had one retry
  });

  it('treats null-request attempts as their own request with no fallback', () => {
    const s = attemptSummary([
      attempt({ event_id: '1', logical_request_id: null }),
      attempt({ event_id: '2', logical_request_id: null }),
    ]);
    expect(s.logicalRequests).toBe(2);
    expect(s.fallbacks).toBe(0);
  });

  it('computes the cache ratio over all token types', () => {
    const s = attemptSummary([
      attempt({
        input_tokens: 100,
        output_tokens: 100,
        cache_read_tokens: 300,
        cache_write_short_tokens: 0,
        cache_write_long_tokens: 0,
        reasoning_tokens: 0,
      }),
    ]);
    // 300 cache read of 500 total.
    expect(s.totalTokens).toBe(500);
    expect(s.cacheRatio).toBeCloseTo(0.6);
  });

  it('is empty-safe', () => {
    const s = attemptSummary([]);
    expect(s.attempts).toBe(0);
    expect(s.cacheRatio).toBe(0);
  });
});

describe('latencyValues', () => {
  it('extracts non-null latencies', () => {
    expect(
      latencyValues([
        attempt({ latency_ms: 500 }),
        attempt({ latency_ms: null }),
        attempt({ latency_ms: 1500 }),
      ])
    ).toEqual([500, 1500]);
  });
});

describe('percentile', () => {
  it('returns null for an empty set', () => {
    expect(percentile([], 50)).toBeNull();
  });

  it('computes nearest-rank percentiles', () => {
    const values = [10, 20, 30, 40, 50];
    expect(percentile(values, 50)).toBe(30);
    expect(percentile(values, 95)).toBe(50);
    expect(percentile(values, 0)).toBe(10);
  });
});
