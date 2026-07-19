// Client-side aggregation of daily_rollups rows (FR-UI-003, FR-QUERY-014).
//
// The v2 /usage endpoint bounds its range (query_max_range_days), so a
// full-history daily trend is served from /api/v2/rollups instead, which is
// unbounded and pre-aggregated to daily grain. Each rollup row is one
// (day, provider, model, machine, project, source, environment, billing_mode)
// cell; a chart needs those rolled up by a single dimension (the day, or the
// chosen breakdown dimension). aggregateRollups does that grouping and returns
// UsageRowV2-shaped buckets so the same composition builders (V2_TOKEN_COMPONENTS)
// render them. Pure and unit-tested; the view owns the paginated fetch.

import type { RollupV2, UsageRowV2 } from '@/api/types-v2';

/** The dimensions a rollup set can be grouped by (its key accessors). */
export const ROLLUP_DIMENSIONS: Record<string, (row: RollupV2) => string> = {
  day: (r) => r.day,
  provider: (r) => r.provider,
  model: (r) => r.model,
  machine: (r) => r.machine,
  project: (r) => r.project,
  source: (r) => r.source,
  environment: (r) => r.environment,
};

/** An empty usage bucket for a key (attempt_count is unused for rollup aggregates). */
function emptyBucket(key: string): UsageRowV2 {
  return {
    key,
    input_tokens: 0,
    output_tokens: 0,
    cache_read_tokens: 0,
    cache_write_short_tokens: 0,
    cache_write_long_tokens: 0,
    reasoning_tokens: 0,
    total_tokens: 0,
    attempt_count: 0,
  };
}

/**
 * Group rollup rows by the given key accessor, summing every token counter, and
 * return the buckets as UsageRowV2s (so the v2 composition builders render them).
 * Buckets are returned in first-seen order; callers sort as needed.
 */
export function aggregateRollups(
  rows: RollupV2[],
  keyOf: (row: RollupV2) => string
): UsageRowV2[] {
  const byKey = new Map<string, UsageRowV2>();
  for (const row of rows) {
    const key = keyOf(row);
    let acc = byKey.get(key);
    if (!acc) {
      acc = emptyBucket(key);
      byKey.set(key, acc);
    }
    acc.input_tokens += row.input_tokens;
    acc.output_tokens += row.output_tokens;
    acc.cache_read_tokens += row.cache_read_tokens;
    acc.cache_write_short_tokens += row.cache_write_short_tokens;
    acc.cache_write_long_tokens += row.cache_write_long_tokens;
    acc.reasoning_tokens += row.reasoning_tokens;
    acc.total_tokens += row.total_tokens;
  }
  return [...byKey.values()];
}
