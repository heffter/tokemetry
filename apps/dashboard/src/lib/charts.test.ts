import { describe, expect, it } from 'vitest';
import {
  componentTableRows,
  groupedBarOption,
  logVisualPieces,
  stackedComponentBarOption,
  stackedTokenBarOption,
  trackedTokensV2,
  V2_TOKEN_COMPONENTS,
} from './charts';
import type { UsageBucket } from '@/api/types';
import type { UsageRowV2 } from '@/api/types-v2';

const RAMP = ['a', 'b', 'c', 'd', 'e'];

const BUCKET: UsageBucket = {
  key: 'm',
  input_tokens: 100,
  output_tokens: 100,
  cache_read_tokens: 800,
  cache_write_short_tokens: 0,
  cache_write_long_tokens: 0,
  total_tokens: 1000,
  cost_usd: null,
};

type Ser = { name: string; data: number[] };

describe('stackedTokenBarOption normalized selection', () => {
  it('normalizes over all components by default (visible sum = 100)', () => {
    const opt = stackedTokenBarOption(['m'], [BUCKET], { normalized: true });
    const series = opt.series as Ser[];
    const total = series.reduce((s, ser) => s + ser.data[0], 0);
    expect(total).toBeCloseTo(100);
  });

  it('re-normalizes to visible components when cache read is hidden', () => {
    const opt = stackedTokenBarOption(['m'], [BUCKET], {
      normalized: true,
      selected: { 'cache read': false },
    });
    const series = opt.series as Ser[];
    // input=100, output=100 of a 200 visible total -> 50% each.
    expect(series.find((s) => s.name === 'input')!.data[0]).toBeCloseTo(50);
    expect(series.find((s) => s.name === 'output')!.data[0]).toBeCloseTo(50);
    const legend = opt.legend as { selected: Record<string, boolean> };
    expect(legend.selected['cache read']).toBe(false);
  });
});

const V2_ROW: UsageRowV2 = {
  key: 'm',
  input_tokens: 100,
  output_tokens: 100,
  cache_read_tokens: 700,
  cache_write_short_tokens: 0,
  cache_write_long_tokens: 0,
  reasoning_tokens: 100,
  total_tokens: 1000,
  attempt_count: 3,
};

describe('v2 token composition', () => {
  it('tracks reasoning as a first-class component', () => {
    expect(trackedTokensV2(V2_ROW)).toBe(1000);
    const labels = V2_TOKEN_COMPONENTS.map((c) => c.label);
    expect(labels).toContain('reasoning');
    expect(
      V2_TOKEN_COMPONENTS.find((c) => c.label === 'reasoning')!.get(V2_ROW)
    ).toBe(100);
  });

  it('reconciles total beyond the tracked components into "other"', () => {
    const withUntracked: UsageRowV2 = { ...V2_ROW, total_tokens: 1200 };
    const other = V2_TOKEN_COMPONENTS.find((c) => c.label === 'other')!;
    expect(other.get(withUntracked)).toBe(200);
    // Never negative when the total is below the tracked sum.
    expect(other.get({ ...V2_ROW, total_tokens: 0 })).toBe(0);
  });

  it('normalizes a v2 composition over all components to 100', () => {
    const opt = stackedComponentBarOption(
      ['m'],
      [V2_ROW],
      V2_TOKEN_COMPONENTS,
      {
        normalized: true,
      }
    );
    const series = opt.series as { name: string; data: number[] }[];
    const total = series.reduce((s, ser) => s + ser.data[0], 0);
    expect(total).toBeCloseTo(100);
  });

  it('builds accessible rows including the reasoning column', () => {
    const rows = componentTableRows(
      [V2_ROW],
      V2_TOKEN_COMPONENTS,
      (r) => r.key
    );
    // label + 7 components + total = 9 cells; reasoning is the 4th component.
    expect(rows[0]).toHaveLength(9);
    expect(rows[0][0]).toBe('m');
    expect(rows[0][rows[0].length - 1]).toBe('1.0K');
  });
});

describe('groupedBarOption', () => {
  it('renders each measure as its own bar series with no stacking', () => {
    const opt = groupedBarOption(
      ['anthropic', 'openai'],
      [
        { name: 'API spend', values: [1, 2] },
        { name: 'Subscription value', values: [10, 20] },
      ]
    );
    const series = opt.series as { name: string; stack?: string }[];
    expect(series).toHaveLength(2);
    expect(series.map((s) => s.name)).toEqual([
      'API spend',
      'Subscription value',
    ]);
    // FR-COST-012: the two metrics must never be summed into one column.
    for (const s of series) expect(s.stack).toBeUndefined();
  });
});

describe('logVisualPieces', () => {
  it('returns a single piece when there is no positive data', () => {
    expect(logVisualPieces([0, 0], RAMP)).toEqual([{ min: 0, color: 'a' }]);
  });

  it('spreads pieces across log decades, contiguous and ascending', () => {
    const pieces = logVisualPieces([100, 1000, 500_000, 200_000_000], RAMP);
    expect(pieces.length).toBeGreaterThan(1);
    expect(pieces.length).toBeLessThanOrEqual(RAMP.length);
    // Open-ended below the first piece and above the last.
    expect(pieces[0].min).toBeUndefined();
    expect(pieces[pieces.length - 1].max).toBeUndefined();
    // Contiguous: each piece's max is the next piece's min.
    for (let i = 0; i < pieces.length - 1; i += 1) {
      expect(pieces[i].max).toBe(pieces[i + 1].min);
    }
    // Colors taken from the ramp in order.
    pieces.forEach((piece, i) => expect(piece.color).toBe(RAMP[i]));
  });
});
