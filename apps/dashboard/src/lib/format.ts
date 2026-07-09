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
