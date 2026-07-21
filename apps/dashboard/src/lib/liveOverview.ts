// Live-overview display helpers, extracted from NowView so the derivations are
// unit-testable. Mirrors GET /api/v2/summary/live-overview (Task 73).
import type { LiveOverviewResponse, ProviderLimitLive } from '@/api/client';

export interface LiveOverviewSummary {
  burnRatePerMin: number;
  todayTotalTokens: number;
  topModel: string | null;
  // The provider limit predicted to exhaust soonest, if any.
  soonestExhaustion: ProviderLimitLive | null;
}

export function summarizeLiveOverview(
  overview: LiveOverviewResponse
): LiveOverviewSummary {
  const todayTotalTokens = overview.today_by_model.reduce(
    (sum, model) => sum + model.total_tokens,
    0
  );
  const topModel =
    overview.today_by_model.length > 0
      ? overview.today_by_model.reduce((best, model) =>
          model.total_tokens > best.total_tokens ? model : best
        ).native_model
      : null;
  const exhausting = overview.provider_limits.filter(
    (limit) => limit.predicted_exhaustion_at !== null
  );
  const soonestExhaustion =
    exhausting.length > 0
      ? exhausting.reduce((soonest, limit) =>
          // ISO-8601 strings compare lexically in timestamp order.
          limit.predicted_exhaustion_at! < soonest.predicted_exhaustion_at!
            ? limit
            : soonest
        )
      : null;
  return {
    burnRatePerMin: overview.burn_rate_per_min,
    todayTotalTokens,
    topModel,
    soonestExhaustion,
  };
}

// A short label for a provider limit window and its utilization.
export function limitLabel(limit: ProviderLimitLive): string {
  return `${limit.provider} · ${limit.window_kind}: ${limit.utilization_pct.toFixed(0)}%`;
}
