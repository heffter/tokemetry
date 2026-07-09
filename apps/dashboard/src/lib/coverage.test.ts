import { describe, expect, it } from 'vitest';
import { cacheSavingsUsd, costIsTrustworthy, pricedCoverage } from './coverage';

describe('pricedCoverage', () => {
  it('is fully covered when all buckets are priced', () => {
    const c = pricedCoverage([
      { key: 'a', total_tokens: 100, cost_usd: '1' },
      { key: 'b', total_tokens: 300, cost_usd: '2' },
    ]);
    expect(c.ratio).toBe(1);
    expect(c.unpricedKeys).toEqual([]);
    expect(costIsTrustworthy(c)).toBe(true);
  });

  it('reports the unpriced fraction and keys', () => {
    const c = pricedCoverage([
      { key: 'opus', total_tokens: 900, cost_usd: null },
      { key: 'haiku', total_tokens: 100, cost_usd: '0.5' },
    ]);
    expect(c.totalTokens).toBe(1000);
    expect(c.pricedTokens).toBe(100);
    expect(c.ratio).toBeCloseTo(0.1);
    expect(c.unpricedKeys).toEqual(['opus']);
    expect(costIsTrustworthy(c)).toBe(false);
  });

  it('treats no usage as fully covered', () => {
    const c = pricedCoverage([]);
    expect(c.ratio).toBe(1);
    expect(costIsTrustworthy(c)).toBe(true);
  });

  it('is trustworthy exactly at the threshold', () => {
    const c = pricedCoverage([
      { key: 'a', total_tokens: 90, cost_usd: '1' },
      { key: 'b', total_tokens: 10, cost_usd: null },
    ]);
    expect(c.ratio).toBeCloseTo(0.9);
    expect(costIsTrustworthy(c)).toBe(true);
  });
});

describe('cacheSavingsUsd', () => {
  const prices = [
    { model: 'opus', input_per_mtok: '15', cache_read_per_mtok: '1.5' },
  ];

  it('sums cache_read x (input - cache_read) per priced model', () => {
    // 1M cache-read tokens at (15 - 1.5)/MTok = $13.50 saved.
    const saved = cacheSavingsUsd(
      [{ key: 'opus', cache_read_tokens: 1_000_000 }],
      prices
    );
    expect(saved).toBeCloseTo(13.5);
  });

  it('ignores models without a price', () => {
    const saved = cacheSavingsUsd(
      [{ key: 'unknown', cache_read_tokens: 5_000_000 }],
      prices
    );
    expect(saved).toBe(0);
  });

  it('uses the latest rate when a model has several', () => {
    const saved = cacheSavingsUsd(
      [{ key: 'opus', cache_read_tokens: 1_000_000 }],
      [
        { model: 'opus', input_per_mtok: '10', cache_read_per_mtok: '1' },
        { model: 'opus', input_per_mtok: '20', cache_read_per_mtok: '2' },
      ]
    );
    expect(saved).toBeCloseTo(18);
  });
});
