import { describe, expect, it } from 'vitest';
import { sparkGeometry } from './sparkline';

describe('sparkGeometry', () => {
  it('is empty for no values', () => {
    const g = sparkGeometry([], 100, 20, 100);
    expect(g.points).toBe('');
    expect(g.last).toBeNull();
  });

  it('maps values into the box with 0 at the bottom', () => {
    const g = sparkGeometry([0, 100], 100, 20, 100);
    // two points across the full width; 0 -> bottom (y=20), 100 -> top (y=0)
    expect(g.points).toBe('0,20 100,0');
    expect(g.last).toEqual([100, 0]);
  });

  it('clamps values above max', () => {
    const g = sparkGeometry([200], 100, 20, 100);
    expect(g.last?.[1]).toBe(0); // clamped to the top
  });

  it('draws a dashed projection past the last point', () => {
    const g = sparkGeometry([50, 60], 100, 20, 100, 100);
    expect(g.projection).not.toBe('');
    // projection starts at the last real point
    expect(g.projection.startsWith('100,')).toBe(true);
  });

  it('has no projection when none is given', () => {
    expect(sparkGeometry([1, 2, 3], 100, 20, 100).projection).toBe('');
  });
});
