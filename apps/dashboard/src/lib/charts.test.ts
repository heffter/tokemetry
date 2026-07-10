import { describe, expect, it } from 'vitest';
import { logVisualPieces } from './charts';

const RAMP = ['a', 'b', 'c', 'd', 'e'];

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
