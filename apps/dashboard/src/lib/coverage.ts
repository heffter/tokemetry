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

/** Coverage below this ratio means a cost total should not be presented bare. */
export const COVERAGE_THRESHOLD = 0.9;

/** True when a cost figure is trustworthy enough to present as-is. */
export function costIsTrustworthy(coverage: Coverage): boolean {
  return coverage.ratio >= COVERAGE_THRESHOLD;
}
