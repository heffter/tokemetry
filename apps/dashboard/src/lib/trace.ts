// Attempt-chain and attempt-aggregate logic for the requests/attempts trace
// view (FR-UI-004/005/006/007). A logical request may span several attempts
// when routing falls back across models or providers; these helpers order a
// chain for display and derive failure and latency aggregates from raw
// attempts. Pure and unit-tested; the view renders the results.

import type { AttemptV2 } from '@/api/types-v2';

/**
 * Attempts in chain order: by start time ascending, tolerant of out-of-order
 * input (a fallback attempt can be recorded before the primary completes). An
 * unparseable timestamp sorts first; equal timestamps keep input order.
 */
export function orderAttempts(attempts: AttemptV2[]): AttemptV2[] {
  return attempts
    .map((attempt, index) => ({ attempt, index }))
    .sort((a, b) => {
      const ta = new Date(a.attempt.ts_started).getTime() || 0;
      const tb = new Date(b.attempt.ts_started).getTime() || 0;
      return ta - tb || a.index - b.index;
    })
    .map((entry) => entry.attempt);
}

/** Failure counts and rate for one grouping key (provider or model). */
export interface FailureStat {
  key: string;
  total: number;
  failures: number;
  /** failures / total in [0, 1]; 0 when there are no attempts. */
  rate: number;
}

/** Failure rate grouped by a key accessor, sorted by descending rate. */
export function failureRateBy(
  attempts: AttemptV2[],
  keyOf: (attempt: AttemptV2) => string
): FailureStat[] {
  const counts = new Map<string, { total: number; failures: number }>();
  for (const attempt of attempts) {
    const key = keyOf(attempt);
    const entry = counts.get(key) ?? { total: 0, failures: 0 };
    entry.total += 1;
    if (!attempt.success) entry.failures += 1;
    counts.set(key, entry);
  }
  return [...counts.entries()]
    .map(([key, entry]) => ({
      key,
      total: entry.total,
      failures: entry.failures,
      rate: entry.total === 0 ? 0 : entry.failures / entry.total,
    }))
    .sort((a, b) => b.rate - a.rate);
}

/** Attempt-derived rollup for a session drilldown (FR-TRACE-012). */
export interface AttemptSummary {
  attempts: number;
  successes: number;
  failures: number;
  totalTokens: number;
  cacheReadTokens: number;
  /** cache_read / total tokens in [0, 1]; 0 when no tokens. */
  cacheRatio: number;
  /** Distinct logical requests (a null id counts as its own request). */
  logicalRequests: number;
  /** Extra attempts beyond the first within a logical request (retries/failover). */
  fallbacks: number;
}

/** Summarize a set of attempts into session-level, provider-neutral stats. */
export function attemptSummary(attempts: AttemptV2[]): AttemptSummary {
  const groups = new Map<string, number>();
  let nullRequests = 0;
  let successes = 0;
  let totalTokens = 0;
  let cacheReadTokens = 0;
  for (const attempt of attempts) {
    if (attempt.success) successes += 1;
    totalTokens +=
      attempt.input_tokens +
      attempt.output_tokens +
      attempt.cache_read_tokens +
      attempt.cache_write_short_tokens +
      attempt.cache_write_long_tokens +
      attempt.reasoning_tokens;
    cacheReadTokens += attempt.cache_read_tokens;
    if (attempt.logical_request_id) {
      groups.set(
        attempt.logical_request_id,
        (groups.get(attempt.logical_request_id) ?? 0) + 1
      );
    } else {
      nullRequests += 1;
    }
  }
  let fallbacks = 0;
  for (const size of groups.values()) fallbacks += size - 1;
  return {
    attempts: attempts.length,
    successes,
    failures: attempts.length - successes,
    totalTokens,
    cacheReadTokens,
    cacheRatio: totalTokens === 0 ? 0 : cacheReadTokens / totalTokens,
    logicalRequests: groups.size + nullRequests,
    fallbacks,
  };
}

/** The non-null latency measurements across attempts, in ms. */
export function latencyValues(attempts: AttemptV2[]): number[] {
  return attempts
    .map((attempt) => attempt.latency_ms)
    .filter((value): value is number => value !== null);
}

/**
 * The p-th percentile (0-100) of a value set by nearest-rank, or null when
 * empty. Used for the latency summary (p50/p95) without a charting dependency.
 */
export function percentile(values: number[], p: number): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const rank = Math.ceil((p / 100) * sorted.length);
  const index = Math.min(sorted.length - 1, Math.max(0, rank - 1));
  return sorted[index];
}
