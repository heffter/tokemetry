// Usage filter model and range-preset date math, shared by the FilterBar and
// the views it drives. Pure functions so the date arithmetic is unit-tested.

export type RangePreset = 'today' | '7d' | '30d' | '90d' | 'all' | 'custom';

export interface UsageFilter {
  from?: string;
  to?: string;
  machine?: string;
  project?: string;
}

/** The fixed presets shown as buttons, in display order. */
export const PRESETS: { key: RangePreset; label: string }[] = [
  { key: 'today', label: 'Today' },
  { key: '7d', label: '7d' },
  { key: '30d', label: '30d' },
  { key: '90d', label: '90d' },
  { key: 'all', label: 'All' },
];

/** ISO calendar day (YYYY-MM-DD) for a Date. */
export function isoDay(date: Date): string {
  return date.toISOString().slice(0, 10);
}

/** Inclusive list of YYYY-MM-DD days from ``from`` to ``to`` (capped at 800).
 *
 * Used to gap-fill a daily chart so calendar days with no usage render as zero
 * rather than being silently spliced out of a category axis.
 */
export function enumerateDays(from: string, to: string): string[] {
  const start = new Date(`${from}T00:00:00Z`);
  const end = new Date(`${to}T00:00:00Z`);
  if (
    Number.isNaN(start.getTime()) ||
    Number.isNaN(end.getTime()) ||
    start > end
  ) {
    return [];
  }
  const days: string[] = [];
  const cursor = new Date(start);
  while (cursor <= end && days.length < 800) {
    days.push(isoDay(cursor));
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }
  return days;
}

const DAYS_BACK: Record<string, number> = {
  today: 0,
  '7d': 6,
  '30d': 29,
  '90d': 89,
};

/** Resolve a fixed preset to an inclusive {from, to} day range.
 *
 * "all" starts at a date safely before any Claude Code usage; "custom" is
 * handled by the caller and returns today/today here as a fallback.
 */
export function presetRange(
  preset: RangePreset,
  now: Date = new Date()
): { from: string; to: string } {
  const to = isoDay(now);
  if (preset === 'all') return { from: '2020-01-01', to };
  const from = new Date(now);
  from.setDate(from.getDate() - (DAYS_BACK[preset] ?? 0));
  return { from: isoDay(from), to };
}
