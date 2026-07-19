import { describe, expect, it } from 'vitest';
import { aggregateRollups, ROLLUP_DIMENSIONS } from './rollups';
import type { RollupV2 } from '@/api/types-v2';

function rollup(overrides: Partial<RollupV2> = {}): RollupV2 {
  return {
    id: 1,
    day: '2026-07-01',
    provider: 'anthropic',
    model: 'claude-opus-4-5',
    machine: 'box-1',
    project: 'proj-a',
    source: 'collector',
    environment: 'prod',
    billing_mode: 'subscription',
    provenance: 'derived',
    input_tokens: 10,
    output_tokens: 5,
    cache_read_tokens: 20,
    cache_write_short_tokens: 1,
    cache_write_long_tokens: 2,
    reasoning_tokens: 3,
    total_tokens: 41,
    cost_usd: null,
    cost_priced_usd: null,
    cost_partial_usd: null,
    cost_estimated_usd: null,
    unpriced_event_count: 0,
    subscription_value_usd: null,
    ...overrides,
  };
}

describe('aggregateRollups', () => {
  it('returns nothing for no rows', () => {
    expect(aggregateRollups([], ROLLUP_DIMENSIONS.day)).toEqual([]);
  });

  it('sums every token counter within a day across dimension combos', () => {
    const rows = [
      rollup({ machine: 'box-1', input_tokens: 10, total_tokens: 41 }),
      rollup({ machine: 'box-2', input_tokens: 30, total_tokens: 60 }),
    ];
    const [bucket] = aggregateRollups(rows, ROLLUP_DIMENSIONS.day);
    expect(bucket.key).toBe('2026-07-01');
    expect(bucket.input_tokens).toBe(40);
    expect(bucket.total_tokens).toBe(101);
    expect(bucket.reasoning_tokens).toBe(6);
  });

  it('groups by an arbitrary dimension (model) across days', () => {
    const rows = [
      rollup({ day: '2026-07-01', model: 'claude-opus-4-5', total_tokens: 41 }),
      rollup({ day: '2026-07-02', model: 'claude-opus-4-5', total_tokens: 41 }),
      rollup({ day: '2026-07-02', model: 'gpt-5', total_tokens: 10 }),
    ];
    const buckets = aggregateRollups(rows, ROLLUP_DIMENSIONS.model);
    const byKey = Object.fromEntries(
      buckets.map((b) => [b.key, b.total_tokens])
    );
    expect(byKey['claude-opus-4-5']).toBe(82);
    expect(byKey['gpt-5']).toBe(10);
  });
});
