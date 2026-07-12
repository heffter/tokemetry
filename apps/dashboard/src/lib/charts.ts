// ECharts option builders. Kept out of views so chart styling is consistent
// and theme-aware. Text/axis colors come from the CSS theme tokens; series
// colors from the validated categorical palette.

import type { EChartsCoreOption } from 'echarts';
import { isDark, seriesColor } from './palette';
import { formatTokens } from './format';
import type { PunchCell, UsageBucket } from '@/api/types';

/** Format an axis/tooltip token value compactly. */
function tokenValue(value: unknown): string {
  return formatTokens(Number(value));
}

/** Sequential blue ramp (light->dark on light surface, dark->bright on dark). */
function blueRamp(dark: boolean): string[] {
  return dark
    ? ['#0d366b', '#184f95', '#256abf', '#3987e5', '#86b6ef']
    : ['#cde2fb', '#9ec5f4', '#5598e7', '#2a78d6', '#184f95'];
}

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

/** A weekday x hour punch card heatmap (sequential blue). */
export function punchCardOption(cells: PunchCell[]): EChartsCoreOption {
  const theme = ink();
  return {
    grid: { top: 12, right: 12, bottom: 60, left: 44 },
    tooltip: {
      position: 'top',
      formatter: (p: unknown) => {
        const params = p as { value: [number, number, number] };
        return `${WEEKDAYS[params.value[1]]} ${params.value[0]}:00 — ${formatTokens(params.value[2])}`;
      },
    },
    xAxis: {
      type: 'category',
      data: Array.from({ length: 24 }, (_, h) => `${h}`),
      splitArea: { show: true },
      axisLabel: { color: theme.muted },
    },
    yAxis: {
      type: 'category',
      data: WEEKDAYS,
      splitArea: { show: true },
      axisLabel: { color: theme.muted },
    },
    visualMap: {
      type: 'piecewise',
      pieces: logVisualPieces(
        cells.map((c) => c.total_tokens),
        blueRamp(isDark())
      ),
      orient: 'horizontal',
      left: 'center',
      bottom: 8,
      textStyle: { color: theme.muted },
    },
    series: [
      {
        type: 'heatmap',
        data: cells.map((c) => [c.hour, c.weekday, c.total_tokens]),
        itemStyle: { borderColor: theme.surface, borderWidth: 1 },
      },
    ],
  };
}

/** A GitHub-style daily contribution calendar for the given date range. */
export function calendarOption(days: UsageBucket[]): EChartsCoreOption {
  const theme = ink();
  const dates = days.map((d) => d.key).sort();
  const range =
    dates.length > 0 ? [dates[0], dates[dates.length - 1]] : undefined;
  return {
    tooltip: {
      formatter: (p: unknown) => {
        const params = p as { value: [string, number] };
        return `${params.value[0]} — ${formatTokens(params.value[1])}`;
      },
    },
    visualMap: {
      type: 'piecewise',
      pieces: logVisualPieces(
        days.map((d) => d.total_tokens),
        blueRamp(isDark())
      ),
      orient: 'horizontal',
      left: 'center',
      bottom: 0,
      textStyle: { color: theme.muted },
    },
    calendar: {
      top: 20,
      left: 40,
      right: 10,
      cellSize: ['auto', 16],
      range,
      itemStyle: { borderColor: theme.surface, color: theme.surface },
      splitLine: { lineStyle: { color: theme.grid } },
      dayLabel: { color: theme.muted },
      monthLabel: { color: theme.muted },
      yearLabel: { show: false },
    },
    series: [
      {
        type: 'heatmap',
        coordinateSystem: 'calendar',
        data: days.map((d) => [d.key, d.total_tokens]),
      },
    ],
  };
}

/** Sum of the five explicitly-tracked token components in a bucket. */
export function trackedTokens(b: UsageBucket): number {
  return (
    b.input_tokens +
    b.output_tokens +
    b.cache_read_tokens +
    b.cache_write_short_tokens +
    b.cache_write_long_tokens
  );
}

/** The token components, in a fixed hue order, with an accessor.
 *
 * "other" reconciles the five tracked components with total_tokens: it is
 * whatever the total carries beyond them (historical bootstrap aggregates and
 * any token type without a per-event breakdown). Without it, buckets whose
 * total exceeds the tracked sum draw a stacked bar shorter than their labelled
 * total, and a total-only bootstrap bucket would render at zero height.
 */
export const TOKEN_COMPONENTS: {
  label: string;
  get: (b: UsageBucket) => number;
}[] = [
  { label: 'input', get: (b) => b.input_tokens },
  { label: 'output', get: (b) => b.output_tokens },
  { label: 'cache read', get: (b) => b.cache_read_tokens },
  { label: 'cache write 5m', get: (b) => b.cache_write_short_tokens },
  { label: 'cache write 1h', get: (b) => b.cache_write_long_tokens },
  {
    label: 'other',
    get: (b) => Math.max(0, b.total_tokens - trackedTokens(b)),
  },
];

/** Header labels for the token-composition accessible table (value columns). */
export const TOKEN_TABLE_HEADERS = [
  'Input',
  'Output',
  'Cache read',
  'Write 5m',
  'Write 1h',
  'Other',
  'Total',
];

/** Build accessible-table rows (label + formatted components + total). */
export function tokenTableRows(
  buckets: UsageBucket[],
  label: (b: UsageBucket) => string
): string[][] {
  return buckets.map((b) => [
    label(b),
    ...TOKEN_COMPONENTS.map((c) => formatTokens(c.get(b))),
    formatTokens(b.total_tokens),
  ]);
}

interface ThemeInk {
  text: string;
  muted: string;
  grid: string;
  surface: string;
}

function ink(): ThemeInk {
  if (typeof getComputedStyle === 'undefined') {
    return {
      text: '#0b0b0b',
      muted: '#898781',
      grid: '#e1e0d9',
      surface: '#fcfcfb',
    };
  }
  const style = getComputedStyle(document.documentElement);
  const read = (name: string, fallback: string): string =>
    style.getPropertyValue(name).trim() || fallback;
  return {
    text: read('--text-primary', '#0b0b0b'),
    muted: read('--text-muted', '#898781'),
    grid: read('--gridline', '#e1e0d9'),
    surface: read('--surface', '#fcfcfb'),
  };
}

/** Options shared by the composition (stacked) builders. */
export interface StackOptions {
  /** Render each category as a 0-100% composition instead of absolute tokens. */
  normalized?: boolean;
  /** Legend selection: series name -> visible. Absent name = visible. Drives
   *  legend.selected AND the normalized denominator (which sums only visible
   *  series, so hiding cache-read re-normalizes the rest to 100%). */
  selected?: Record<string, boolean>;
}

/** True when a series name is currently visible (absent = visible). */
function isSelected(
  selected: Record<string, boolean> | undefined,
  name: string
): boolean {
  return selected?.[name] !== false;
}

/** Options for single-measure bar charts over skewed data. */
export interface BarOptions {
  /** Use a log value axis so one outlier does not crush the other bars. */
  log?: boolean;
}

/** Category axis config + the bottom margin its labels need.
 *
 * Long or numerous labels rotate and truncate (full text stays in the axis
 * tooltip), and the grid reserves enough room so a rotated label is not clipped
 * by a fixed container height.
 */
function categoryAxis(
  categories: string[],
  theme: ThemeInk
): { axis: Record<string, unknown>; bottom: number } {
  const longest = categories.reduce((m, c) => Math.max(m, c.length), 0);
  const rotate = categories.length > 6 || longest > 12 ? 40 : 0;
  return {
    axis: {
      type: 'category',
      data: categories,
      axisLabel: {
        color: theme.muted,
        rotate,
        overflow: 'truncate',
        width: 120,
      },
      axisLine: { lineStyle: { color: theme.grid } },
    },
    bottom: rotate ? 92 : 40,
  };
}

/** Piecewise visualMap pieces on log-decade boundaries for power-law data.
 *
 * A linear color scale over a 1000x range renders almost every cell in the
 * palest step with one dark outlier; log-decade buckets spread the contrast
 * across the range so the distribution is readable.
 */
export function logVisualPieces(
  values: number[],
  ramp: string[]
): { min?: number; max?: number; color: string }[] {
  const nonzero = values.filter((v) => v > 0);
  if (nonzero.length === 0) return [{ min: 0, color: ramp[0] }];
  const lo = Math.floor(Math.log10(Math.min(...nonzero)));
  const hi = Math.ceil(Math.log10(Math.max(...nonzero)));
  const decades = Math.max(1, hi - lo);
  const steps = Math.min(ramp.length, decades);
  const width = decades / steps;
  const pieces: { min?: number; max?: number; color: string }[] = [];
  for (let i = 0; i < steps; i += 1) {
    const min = i === 0 ? undefined : Math.round(10 ** (lo + i * width));
    const max =
      i === steps - 1 ? undefined : Math.round(10 ** (lo + (i + 1) * width));
    pieces.push({ min, max, color: ramp[i] });
  }
  return pieces;
}

/** A vertical bar chart of a single measure across categories. */
export function barOption(
  categories: string[],
  values: number[],
  name: string,
  opts: BarOptions = {}
): EChartsCoreOption {
  const theme = ink();
  const { axis, bottom } = categoryAxis(categories, theme);
  return {
    grid: { top: 24, right: 16, bottom, left: 64 },
    tooltip: { trigger: 'axis', valueFormatter: tokenValue },
    xAxis: axis,
    yAxis: {
      type: opts.log ? 'log' : 'value',
      min: opts.log ? 1 : undefined,
      axisLabel: { color: theme.muted, formatter: tokenValue },
      splitLine: { lineStyle: { color: theme.grid } },
    },
    series: [
      {
        name,
        type: 'bar',
        data: values,
        barMaxWidth: 40,
        itemStyle: {
          color: seriesColor(0, isDark()),
          borderRadius: [4, 4, 0, 0],
        },
      },
    ],
  };
}

/** A bar chart on a real time axis, so idle gaps render as gaps. */
export function timeBarOption(
  points: [number, number][],
  name: string,
  opts: BarOptions & { axisMax?: number } = {}
): EChartsCoreOption {
  const theme = ink();
  return {
    grid: { top: 24, right: 16, bottom: 40, left: 64 },
    tooltip: { trigger: 'axis', valueFormatter: tokenValue },
    xAxis: {
      type: 'time',
      // Anchor the right edge (e.g. to "now") so a trailing idle gap shows.
      max: opts.axisMax,
      axisLabel: { color: theme.muted },
      axisLine: { lineStyle: { color: theme.grid } },
    },
    yAxis: {
      type: opts.log ? 'log' : 'value',
      min: opts.log ? 1 : undefined,
      axisLabel: { color: theme.muted, formatter: tokenValue },
      splitLine: { lineStyle: { color: theme.grid } },
    },
    series: [
      {
        name,
        type: 'bar',
        data: points,
        itemStyle: {
          color: seriesColor(0, isDark()),
          borderRadius: [4, 4, 0, 0],
        },
      },
    ],
  };
}

/** A stacked bar of the token components per category (hue-ordered).
 *
 * In ``normalized`` mode each bar sums to 100%, so composition is comparable
 * regardless of magnitude -- essential when cache-read is ~95% of every bar and
 * would otherwise crush the other components to sub-pixel slivers.
 */
export function stackedTokenBarOption(
  categories: string[],
  buckets: UsageBucket[],
  opts: StackOptions = {}
): EChartsCoreOption {
  const theme = ink();
  const dark = isDark();
  const norm = opts.normalized === true;
  const { axis, bottom } = categoryAxis(categories, theme);
  // Normalized denominator sums only the visible components, so hiding
  // cache-read re-normalizes the remaining components to fill 100%.
  const visible = TOKEN_COMPONENTS.filter((c) =>
    isSelected(opts.selected, c.label)
  );
  const totals = buckets.map(
    (b) => visible.reduce((sum, c) => sum + c.get(b), 0) || 1
  );
  const pct = (value: unknown): string => `${Number(value).toFixed(1)}%`;
  return {
    grid: { top: 28, right: 16, bottom, left: 64 },
    tooltip: { trigger: 'axis', valueFormatter: norm ? pct : tokenValue },
    legend: {
      top: 0,
      textStyle: { color: theme.text },
      selected: opts.selected,
    },
    xAxis: axis,
    yAxis: {
      type: 'value',
      max: norm ? 100 : undefined,
      axisLabel: {
        color: theme.muted,
        formatter: norm ? '{value}%' : tokenValue,
      },
      splitLine: { lineStyle: { color: theme.grid } },
    },
    series: TOKEN_COMPONENTS.map((component, index) => ({
      name: component.label,
      type: 'bar',
      stack: 'tokens',
      barMaxWidth: 48,
      data: buckets.map((bucket, i) =>
        norm ? (component.get(bucket) / totals[i]) * 100 : component.get(bucket)
      ),
      // A thin surface-colored border separates thin adjacent segments.
      itemStyle: {
        color: seriesColor(index, dark),
        borderColor: theme.surface,
        borderWidth: 1,
      },
    })),
  };
}

/** A stacked area chart over a shared time/category axis. */
export function stackedAreaOption(
  categories: string[],
  series: { name: string; values: number[] }[],
  opts: StackOptions = {}
): EChartsCoreOption {
  const theme = ink();
  const dark = isDark();
  const norm = opts.normalized === true;
  // Denominator sums only visible series so hiding one re-normalizes the rest.
  const totals = categories.map((_, i) =>
    series.reduce(
      (sum, entry) =>
        sum +
        (isSelected(opts.selected, entry.name) ? (entry.values[i] ?? 0) : 0),
      0
    )
  );
  const pct = (value: unknown): string => `${Number(value).toFixed(1)}%`;
  return {
    grid: { top: 24, right: 16, bottom: 48, left: 64 },
    tooltip: { trigger: 'axis', valueFormatter: norm ? pct : tokenValue },
    legend: {
      top: 0,
      textStyle: { color: theme.text },
      selected: opts.selected,
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: categories,
      axisLabel: { color: theme.muted },
      axisLine: { lineStyle: { color: theme.grid } },
    },
    yAxis: {
      type: 'value',
      max: norm ? 100 : undefined,
      axisLabel: {
        color: theme.muted,
        formatter: norm ? '{value}%' : tokenValue,
      },
      splitLine: { lineStyle: { color: theme.grid } },
    },
    series: series.map((entry, index) => ({
      name: entry.name,
      type: 'line',
      stack: 'total',
      // A restrained wash so fills read as tint, not saturated blocks.
      areaStyle: { opacity: 0.25 },
      showSymbol: false,
      lineStyle: { width: 1.5 },
      itemStyle: { color: seriesColor(index, dark) },
      data: entry.values.map((value, i) =>
        norm ? (totals[i] ? (value / totals[i]) * 100 : 0) : value
      ),
    })),
  };
}
