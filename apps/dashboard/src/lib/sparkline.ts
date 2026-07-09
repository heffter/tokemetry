// Pure geometry for a sparkline: turn a series of values into SVG polyline
// points within a box. Kept separate from the component so it is unit-tested.

export interface SparkGeometry {
  /** "x,y x,y …" points for the history polyline. */
  points: string;
  /** "x,y x,y" for the dashed projection segment, or '' when none. */
  projection: string;
  /** [x, y] of the last real point (for a marker). */
  last: [number, number] | null;
}

/**
 * Compute sparkline geometry.
 *
 * @param values history values, oldest first
 * @param width  box width in px
 * @param height box height in px
 * @param max    value mapped to the top of the box (e.g. 100 for percent)
 * @param projected optional projected next value, drawn as a dashed segment
 */
export function sparkGeometry(
  values: number[],
  width: number,
  height: number,
  max: number,
  projected: number | null = null
): SparkGeometry {
  if (values.length === 0) {
    return { points: '', projection: '', last: null };
  }
  const span = Math.max(1, values.length - 1);
  const safeMax = max <= 0 ? 1 : max;
  const x = (i: number): number => (i / span) * width;
  const y = (v: number): number =>
    height - (Math.min(v, safeMax) / safeMax) * height;

  const points = values.map((v, i) => `${x(i)},${y(v)}`).join(' ');
  const lastIndex = values.length - 1;
  const last: [number, number] = [x(lastIndex), y(values[lastIndex])];

  let projection = '';
  if (projected !== null) {
    const px = x(lastIndex) + width / span;
    projection = `${last[0]},${last[1]} ${px},${y(projected)}`;
  }
  return { points, projection, last };
}
