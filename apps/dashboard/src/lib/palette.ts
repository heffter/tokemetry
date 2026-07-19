// Validated categorical palette (dataviz reference instance). Hues are
// assigned in fixed order and never cycled; a 9th series folds into "Other".
// Light/dark steps are selected per surface, not auto-flipped.

export const CATEGORICAL_LIGHT = [
  '#2a78d6', // blue
  '#1baf7a', // aqua
  '#eda100', // yellow
  '#008300', // green
  '#4a3aa7', // violet
  '#e34948', // red
  '#e87ba4', // magenta
  '#eb6834', // orange
];

export const CATEGORICAL_DARK = [
  '#3987e5',
  '#199e70',
  '#c98500',
  '#008300',
  '#9085e9',
  '#e66767',
  '#d55181',
  '#d95926',
];

export const STATUS = {
  good: '#0ca30c',
  warning: '#fab219',
  critical: '#d03b3b',
} as const;

/** Return the categorical hue for a series index in the active theme. */
export function seriesColor(index: number, dark: boolean): string {
  const ramp = dark ? CATEGORICAL_DARK : CATEGORICAL_LIGHT;
  return ramp[index % ramp.length];
}

/**
 * Assign a stable categorical color to each key (e.g. provider or model ids).
 *
 * Keys are deduplicated and sorted so a given key always maps to the same hue
 * regardless of the order or subset in which it is passed -- a provider keeps
 * its color across views (NFR-MAIN-006 wants provider colors to derive from
 * registry data, not per-view chart order). The ramp wraps past its length,
 * matching seriesColor.
 */
export function stableColorMap(
  keys: Iterable<string>,
  dark: boolean
): Map<string, string> {
  const ordered = [...new Set(keys)].sort();
  const map = new Map<string, string>();
  ordered.forEach((key, index) => {
    map.set(key, seriesColor(index, dark));
  });
  return map;
}

/** The stable color for a single key within a registry key set. */
export function keyColor(
  key: string,
  keys: Iterable<string>,
  dark: boolean
): string {
  return stableColorMap(keys, dark).get(key) ?? seriesColor(0, dark);
}

/** True when the document is currently in dark mode. */
export function isDark(): boolean {
  if (typeof document !== 'undefined') {
    const stamped = document.documentElement.dataset.theme;
    if (stamped === 'dark') return true;
    if (stamped === 'light') return false;
  }
  if (typeof window !== 'undefined' && window.matchMedia) {
    return window.matchMedia('(prefers-color-scheme: dark)').matches;
  }
  return false;
}
