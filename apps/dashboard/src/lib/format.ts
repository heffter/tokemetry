// Presentation formatters. Pure functions, unit-tested.

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

/** Format a nullable USD string as "$1.23" (or "—" when unknown). */
export function formatCost(value: string | null): string {
  if (value === null) return '—';
  const amount = Number(value);
  if (Number.isNaN(amount)) return '—';
  if (amount === 0) return '$0.00';
  if (amount < 0.01) return `$${amount.toFixed(4)}`;
  return `$${amount.toFixed(2)}`;
}

/** Format a percentage to one decimal (42.5 -> "42.5%"). */
export function formatPct(value: number): string {
  return `${value.toFixed(1)}%`;
}

/** Human-readable time remaining until an ISO timestamp ("in 43m", "now"). */
export function timeUntil(iso: string | null, now: Date = new Date()): string {
  if (iso === null) return '—';
  const target = new Date(iso).getTime();
  const diffMin = Math.round((target - now.getTime()) / 60000);
  if (diffMin <= 0) return 'now';
  if (diffMin < 60) return `in ${diffMin}m`;
  const hours = Math.floor(diffMin / 60);
  const minutes = diffMin % 60;
  return `in ${hours}h ${minutes}m`;
}

/** Absolute date and time for an ISO timestamp ("Jul 12, 2:30 PM").
 *
 * Rendered in the browser's local timezone. Task 31 routes the timezone
 * preference through here; callers do not change.
 */
export function formatDateTime(iso: string | null): string {
  if (iso === null) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat(undefined, {
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

const WINDOW_LABELS: Record<string, string> = {
  five_hour: '5-hour block',
  seven_day: 'Weekly',
  seven_day_opus: 'Weekly (Opus)',
  seven_day_sonnet: 'Weekly (Sonnet)',
};

/** Friendly label for a limit window kind. */
export function windowLabel(kind: string): string {
  return WINDOW_LABELS[kind] ?? kind;
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
  const match = s.match(/^([a-z]+)-(\d+(?:-\d+)*)$/);
  if (match) {
    const family = match[1][0].toUpperCase() + match[1].slice(1);
    return `${family} ${match[2].replace(/-/g, '.')}`;
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
