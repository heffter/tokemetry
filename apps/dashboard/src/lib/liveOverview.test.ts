import { describe, expect, it } from 'vitest';
import type { LiveOverviewResponse } from '@/api/client';
import { limitLabel, summarizeLiveOverview } from './liveOverview';

function overview(): LiveOverviewResponse {
  return {
    now: '2026-07-10T12:00:00Z',
    burn_rate_per_min: 66.7,
    provider_limits: [
      {
        provider: 'anthropic',
        window_kind: 'five_hour',
        utilization_pct: 50,
        limit_amount: 200000,
        remaining: 5000,
        unit: 'tokens',
        resets_at: '2026-07-10T14:00:00Z',
        predicted_exhaustion_at: '2026-07-10T13:15:00Z',
      },
      {
        provider: 'openai',
        window_kind: 'daily',
        utilization_pct: 20,
        limit_amount: null,
        remaining: null,
        unit: null,
        resets_at: null,
        predicted_exhaustion_at: '2026-07-10T12:45:00Z',
      },
    ],
    today_by_model: [
      { native_model: 'claude-sonnet-4-5', total_tokens: 600 },
      { native_model: 'gpt-5', total_tokens: 100 },
    ],
  };
}

describe('summarizeLiveOverview', () => {
  it('sums today tokens and picks the top model', () => {
    const s = summarizeLiveOverview(overview());
    expect(s.todayTotalTokens).toBe(700);
    expect(s.topModel).toBe('claude-sonnet-4-5');
    expect(s.burnRatePerMin).toBe(66.7);
  });

  it('picks the soonest exhaustion across providers', () => {
    const s = summarizeLiveOverview(overview());
    // openai exhausts at 12:45, before anthropic at 13:15.
    expect(s.soonestExhaustion?.provider).toBe('openai');
  });

  it('reports no exhaustion when none is predicted', () => {
    const o = overview();
    for (const limit of o.provider_limits) limit.predicted_exhaustion_at = null;
    const s = summarizeLiveOverview(o);
    expect(s.soonestExhaustion).toBeNull();
  });

  it('handles an empty overview', () => {
    const s = summarizeLiveOverview({
      now: '2026-07-10T12:00:00Z',
      burn_rate_per_min: 0,
      provider_limits: [],
      today_by_model: [],
    });
    expect(s.todayTotalTokens).toBe(0);
    expect(s.topModel).toBeNull();
    expect(s.soonestExhaustion).toBeNull();
  });
});

describe('limitLabel', () => {
  it('formats provider, window, and utilization', () => {
    expect(limitLabel(overview().provider_limits[0])).toBe(
      'anthropic · five_hour: 50%'
    );
  });
});
