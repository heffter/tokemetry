import { describe, expect, it } from 'vitest';
import { costIsTrustworthy, pricedCoverage } from './coverage';

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
