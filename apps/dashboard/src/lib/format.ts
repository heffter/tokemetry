// Presentation formatters. Pure functions, unit-tested.

import { activeTimezone } from '@/composables/useSettings';

const TOKEN_UNITS = ['', 'K', 'M', 'B', 'T'];

/** Format a token count compactly (1234 -> "1.2K", 5_000_000 -> "5.0M"). */
export function formatTokens(value: number): string {
  if (value < 1000) return String(value);
  let scaled = value;
  let unit = 0;
  while (scaled >= 1000 && unit < TOKEN_UNITS.length - 1) {
    scaled /= 1000;
    unit += 1;
  }
  return `${scaled.toFixed(1)}${TOKEN_UNITS[unit]}`;
}

/** Format a nullable USD string as "$1,234.56" (or "—" when unknown).
 *
 * Uses thousands separators; sub-cent amounts get 4 decimals so tiny per-event
 * costs stay legible.
 */
export function formatCost(value: string | null): string {
  if (value === null) return '—';
  const amount = Number(value);
  if (Number.isNaN(amount)) return '—';
  const digits = amount !== 0 && Math.abs(amount) < 0.01 ? 4 : 2;
  return `$${amount.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

/** Format a percentage to one decimal (42.5 -> "42.5%"). */
export function formatPct(value: number): string {
  return `${value.toFixed(1)}%`;
}

/** Human-readable time to/from an ISO timestamp ("in 43m", "now", "15h ago").
 *
 * A moment in the past returns an explicit "… ago" form rather than "now", so a
 * stale reset (e.g. a block that ended hours back) is never mistaken for a
 * reset that is imminent.
 */
export function timeUntil(iso: string | null, now: Date = new Date()): string {
  if (iso === null) return '—';
  const diffMin = Math.round((new Date(iso).getTime() - now.getTime()) / 60000);
  if (diffMin >= -1 && diffMin <= 0) return 'now';
  if (diffMin < 0) return `${formatDuration(Math.abs(diffMin) * 60)} ago`;
  if (diffMin < 60) return `in ${diffMin}m`;
  const hours = Math.floor(diffMin / 60);
  const minutes = diffMin % 60;
  return `in ${hours}h ${minutes}m`;
}

/** Absolute date and time for an ISO timestamp ("Jul 12, 2:30 PM").
 *
 * Rendered in the user's selected timezone (see useSettings); reading the
 * reactive preference here means every call site re-renders when it changes.
 */
export function formatDateTime(iso: string | null): string {
  if (iso === null) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat(undefined, {
    timeZone: activeTimezone(),
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(date);
}

/** Compact humanized duration from seconds ("45s", "12m", "3h", "2d"). */
export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}

/** Map a utilization percentage to a status ramp key. */
export function utilizationStatus(
  pct: number
): 'good' | 'warning' | 'critical' {
  if (pct >= 95) return 'critical';
  if (pct >= 80) return 'warning';
  return 'good';
}

// The seed labels for the Anthropic windows. These stay as the fallback so a
// pure-Anthropic deployment renders identically until the provider window
// registry (Task 69) supplies labels; a caller may pass a registry-derived map
// to override or extend them for other providers (FR-UI-010/014).
const WINDOW_LABELS: Record<string, string> = {
  five_hour: '5-hour block',
  seven_day: 'Weekly',
  seven_day_opus: 'Weekly (Opus)',
  seven_day_sonnet: 'Weekly (Sonnet)',
};

/** Friendly label for a limit window kind.
 *
 * Resolution order: a registry-supplied ``labels`` map, then the Anthropic
 * seed, then the raw kind (so an unknown window shows its id, not a blank).
 */
export function windowLabel(
  kind: string,
  labels?: Record<string, string>
): string {
  return labels?.[kind] ?? WINDOW_LABELS[kind] ?? kind;
}

// A reset farther out than this reads better as an absolute date than a long
// countdown. Used instead of a provider-specific window-name check so any
// provider's long window (not just Anthropic's "seven_day*") gets a date.
const LONG_RESET_HORIZON_MS = 36 * 3600 * 1000;

/** True when a reset is far enough out to prefer an absolute date over a countdown. */
export function isLongHorizonReset(
  resetsAt: string | null,
  now: Date = new Date()
): boolean {
  if (resetsAt === null) return false;
  const target = new Date(resetsAt).getTime();
  if (Number.isNaN(target)) return false;
  return target - now.getTime() > LONG_RESET_HORIZON_MS;
}

/** Compact relative-past time ("3m ago", "2h ago", "5d ago"). */
export function timeAgo(iso: string, now: Date = new Date()): string {
  const mins = Math.max(0, (now.getTime() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${Math.round(mins)}m ago`;
  if (mins < 1440) return `${Math.floor(mins / 60)}h ago`;
  return `${Math.floor(mins / 1440)}d ago`;
}

/** Humanize a model id ("claude-opus-4-8-20260101" -> "Opus 4.8"). */
export function modelLabel(id: string): string {
  let s = id.replace(/^.*anthropic\./, '').replace(/^claude-/, '');
  // Strip the bedrock version suffix (-v1:0) before the date (-YYYYMMDD),
  // otherwise the date is left dangling at the end.
  s = s.replace(/-v\d+:\d+$/, '').replace(/-\d{8}$/, '');
  // Modern order: family-version (e.g. "opus-4-8" -> "Opus 4.8").
  const modern = s.match(/^([a-z]+)-(\d+(?:-\d+)*)$/);
  if (modern) {
    const family = modern[1][0].toUpperCase() + modern[1].slice(1);
    return `${family} ${modern[2].replace(/-/g, '.')}`;
  }
  // Legacy 3.x order: version-family (e.g. "3-7-sonnet" -> "Sonnet 3.7").
  const legacy = s.match(/^(\d+(?:-\d+)*)-([a-z]+)$/);
  if (legacy) {
    const family = legacy[2][0].toUpperCase() + legacy[2].slice(1);
    return `${family} ${legacy[1].replace(/-/g, '.')}`;
  }
  return id;
}

interface TokenBucket {
  cache_read_tokens: number;
  total_tokens: number;
}

/** Fraction of total tokens (0-1) that were served from cache reads. */
export function cacheReadShare(buckets: TokenBucket[]): number {
  let read = 0;
  let total = 0;
  for (const bucket of buckets) {
    read += bucket.cache_read_tokens;
    total += bucket.total_tokens;
  }
  return total === 0 ? 0 : read / total;
}
