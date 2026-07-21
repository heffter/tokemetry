// Adapt the v2 heatmap response to the chart shapes the existing punch-card and
// calendar options consume, so BreakdownsView can switch to the provider-neutral
// endpoint without changing the chart code (Task 74).
import type { HeatmapV2Response } from '@/api/client';
import type { PunchCell, UsageBucket } from '@/api/types';

export function v2PunchCells(overview: HeatmapV2Response): PunchCell[] {
  return overview.punch_card.map((cell) => ({
    weekday: cell.weekday,
    hour: cell.hour,
    total_tokens: cell.value,
  }));
}

export function v2CalendarBuckets(overview: HeatmapV2Response): UsageBucket[] {
  return overview.calendar.map((cell) => ({
    key: cell.date,
    input_tokens: 0,
    output_tokens: 0,
    cache_read_tokens: 0,
    cache_write_short_tokens: 0,
    cache_write_long_tokens: 0,
    total_tokens: cell.value,
    cost_usd: null,
  }));
}
