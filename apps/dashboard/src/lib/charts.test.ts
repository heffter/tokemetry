import { describe, expect, it } from 'vitest';
import { logVisualPieces, stackedTokenBarOption } from './charts';
import type { UsageBucket } from '@/api/types';

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
