// Priced-coverage math: what fraction of token volume has a known cost.
//
// The default price table can lack the models a user actually runs, so a
// dollar total derived from it is a partial figure. This computes the
// coverage so the UI can gate or annotate cost instead of presenting a lie.

export interface Coverage {
  pricedTokens: number;
  totalTokens: number;
  /** priced / total in [0, 1]; 1 when there is no usage. */
  ratio: number;
  /** Group keys (models) that have usage but no cost. */
  unpricedKeys: string[];
}

interface CostBucket {
  key: string;
  total_tokens: number;
  cost_usd: string | null;
}

/** Compute priced coverage across a set of usage buckets. */
export function pricedCoverage(buckets: CostBucket[]): Coverage {
  let priced = 0;
  let total = 0;
  const unpriced: string[] = [];
  for (const bucket of buckets) {
    total += bucket.total_tokens;
    if (bucket.cost_usd === null) {
      unpriced.push(bucket.key);
    } else {
      priced += bucket.total_tokens;
    }
  }
  return {
    pricedTokens: priced,
    totalTokens: total,
    ratio: total === 0 ? 1 : priced / total,
    unpricedKeys: unpriced,
  };
}

interface CacheBucket {
  key: string;
  cache_read_tokens: number;
}

interface PriceRate {
  model: string;
  input_per_mtok: string;
  cache_read_per_mtok: string;
}

/** Estimated USD saved by cache reads vs paying the input price for them.
 *
 * Per priced model: cache_read_tokens x (input_rate - cache_read_rate). Prices
 * must be ordered by effective_date ascending (as the API returns them) so the
 * latest rate per model wins. Unpriced models contribute nothing.
 */
export function cacheSavingsUsd(
  buckets: CacheBucket[],
  prices: PriceRate[]
): number {
  const rate = new Map<string, { input: number; cacheRead: number }>();
  for (const price of prices) {
    rate.set(price.model, {
      input: Number(price.input_per_mtok),
      cacheRead: Number(price.cache_read_per_mtok),
    });
  }
  let saved = 0;
  for (const bucket of buckets) {
    const r = rate.get(bucket.key);
    if (!r) continue;
    saved += (bucket.cache_read_tokens * (r.input - r.cacheRead)) / 1_000_000;
  }
  return saved;
}

/** Coverage below this ratio means a cost total should not be presented bare. */
export const COVERAGE_THRESHOLD = 0.9;

/** True when a cost figure is trustworthy enough to present as-is. */
export function costIsTrustworthy(coverage: Coverage): boolean {
  return coverage.ratio >= COVERAGE_THRESHOLD;
}
