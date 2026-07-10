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
  const max = Math.max(1, ...cells.map((c) => c.total_tokens));
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
      min: 0,
      max,
      calculable: true,
      orient: 'horizontal',
      left: 'center',
      bottom: 8,
      textStyle: { color: theme.muted },
      inRange: { color: blueRamp(isDark()) },
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
  const max = Math.max(1, ...days.map((d) => d.total_tokens));
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
      min: 0,
      max,
      show: false,
      inRange: { color: blueRamp(isDark()) },
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

/** A vertical bar chart of a single measure across categories. */
export function barOption(
  categories: string[],
  values: number[],
  name: string
): EChartsCoreOption {
  const theme = ink();
  return {
    grid: { top: 24, right: 16, bottom: 48, left: 64 },
    tooltip: { trigger: 'axis', valueFormatter: tokenValue },
    xAxis: {
      type: 'category',
      data: categories,
      axisLabel: { color: theme.muted, rotate: categories.length > 8 ? 40 : 0 },
      axisLine: { lineStyle: { color: theme.grid } },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: theme.muted, formatter: tokenValue },
      splitLine: { lineStyle: { color: theme.grid } },
    },
    series: [
      {
        name,
        type: 'bar',
        data: values,
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
  name: string
): EChartsCoreOption {
  const theme = ink();
  return {
    grid: { top: 24, right: 16, bottom: 40, left: 64 },
    tooltip: { trigger: 'axis', valueFormatter: tokenValue },
    xAxis: {
      type: 'time',
      axisLabel: { color: theme.muted },
      axisLine: { lineStyle: { color: theme.grid } },
    },
    yAxis: {
      type: 'value',
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

/** A stacked bar of the five token components per category (hue-ordered). */
export function stackedTokenBarOption(
  categories: string[],
  buckets: UsageBucket[]
): EChartsCoreOption {
  const theme = ink();
  const dark = isDark();
  return {
    grid: { top: 28, right: 16, bottom: 56, left: 64 },
    tooltip: { trigger: 'axis', valueFormatter: tokenValue },
    legend: { top: 0, textStyle: { color: theme.text } },
    xAxis: {
      type: 'category',
      data: categories,
      axisLabel: {
        color: theme.muted,
        rotate: categories.length > 6 ? 30 : 0,
      },
      axisLine: { lineStyle: { color: theme.grid } },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: theme.muted, formatter: tokenValue },
      splitLine: { lineStyle: { color: theme.grid } },
    },
    series: TOKEN_COMPONENTS.map((component, index) => ({
      name: component.label,
      type: 'bar',
      stack: 'tokens',
      data: buckets.map(component.get),
      itemStyle: { color: seriesColor(index, dark) },
    })),
  };
}

/** A stacked area chart over a shared time/category axis. */
export function stackedAreaOption(
  categories: string[],
  series: { name: string; values: number[] }[]
): EChartsCoreOption {
  const theme = ink();
  const dark = isDark();
  return {
    grid: { top: 24, right: 16, bottom: 48, left: 64 },
    tooltip: { trigger: 'axis', valueFormatter: tokenValue },
    legend: { top: 0, textStyle: { color: theme.text } },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: categories,
      axisLabel: { color: theme.muted },
      axisLine: { lineStyle: { color: theme.grid } },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: theme.muted, formatter: tokenValue },
      splitLine: { lineStyle: { color: theme.grid } },
    },
    series: series.map((entry, index) => ({
      name: entry.name,
      type: 'line',
      stack: 'total',
      areaStyle: { opacity: 0.6 },
      showSymbol: false,
      lineStyle: { width: 2 },
      itemStyle: { color: seriesColor(index, dark) },
      data: entry.values,
    })),
  };
}
