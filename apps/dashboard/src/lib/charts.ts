// ECharts option builders. Kept out of views so chart styling is consistent
// and theme-aware. Text/axis colors come from the CSS theme tokens; series
// colors from the validated categorical palette.

import type { EChartsCoreOption } from 'echarts';
import { isDark, seriesColor } from './palette';
import { formatTokens } from './format';
import type { UsageBucket } from '@/api/types';

/** Format an axis/tooltip token value compactly. */
function tokenValue(value: unknown): string {
  return formatTokens(Number(value));
}

/** The five token components, in a fixed hue order, with an accessor. */
export const TOKEN_COMPONENTS: {
  label: string;
  get: (b: UsageBucket) => number;
}[] = [
  { label: 'input', get: (b) => b.input_tokens },
  { label: 'output', get: (b) => b.output_tokens },
  { label: 'cache read', get: (b) => b.cache_read_tokens },
  { label: 'cache write 5m', get: (b) => b.cache_write_short_tokens },
  { label: 'cache write 1h', get: (b) => b.cache_write_long_tokens },
];

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
