import { describe, expect, it } from 'vitest';
import type { HeatmapV2Response } from '@/api/client';
import { v2CalendarBuckets, v2PunchCells } from './heatmapV2';

const overview: HeatmapV2Response = {
  punch_card: [
    { weekday: 0, hour: 9, value: 157 },
    { weekday: 1, hour: 14, value: 30 },
  ],
  calendar: [
    { date: '2026-07-06', value: 157 },
    { date: '2026-07-07', value: 30 },
  ],
  metadata: {
    total_tokens: 187,
    date_from: '2026-07-01',
    date_to: '2026-07-31',
    applied_filters: {},
  },
};

describe('v2PunchCells', () => {
  it('maps value to total_tokens, keeping weekday and hour', () => {
    expect(v2PunchCells(overview)).toEqual([
      { weekday: 0, hour: 9, total_tokens: 157 },
      { weekday: 1, hour: 14, total_tokens: 30 },
    ]);
  });
});

describe('v2CalendarBuckets', () => {
  it('maps date to key and value to total_tokens', () => {
    const buckets = v2CalendarBuckets(overview);
    expect(buckets[0].key).toBe('2026-07-06');
    expect(buckets[0].total_tokens).toBe(157);
    expect(buckets[0].cost_usd).toBeNull();
  });
});
