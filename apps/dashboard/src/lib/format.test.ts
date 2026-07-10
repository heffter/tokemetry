import { describe, expect, it } from 'vitest';
import {
  cacheReadShare,
  formatCost,
  formatDateTime,
  formatDuration,
  formatPct,
  formatTokens,
  modelLabel,
  timeAgo,
  timeUntil,
  utilizationStatus,
  windowLabel,
} from './format';

describe('formatTokens', () => {
  it('leaves small numbers as-is', () => {
    expect(formatTokens(42)).toBe('42');
  });
  it('scales thousands and millions', () => {
    expect(formatTokens(1234)).toBe('1.2K');
    expect(formatTokens(5_000_000)).toBe('5.0M');
    expect(formatTokens(2_364_494_748)).toBe('2.4B');
  });
});

describe('formatCost', () => {
  it('renders unknown as a dash', () => {
    expect(formatCost(null)).toBe('—');
  });
  it('renders small and normal amounts', () => {
    expect(formatCost('0')).toBe('$0.00');
    expect(formatCost('0.0048')).toBe('$0.0048');
    expect(formatCost('12.5')).toBe('$12.50');
  });
  it('adds thousands separators for large amounts', () => {
    expect(formatCost('1957.9043')).toBe('$1,957.90');
    expect(formatCost('10352.95')).toBe('$10,352.95');
  });
});

describe('formatPct', () => {
  it('formats to one decimal', () => {
    expect(formatPct(42.5)).toBe('42.5%');
  });
});

describe('formatDateTime', () => {
  it('renders null as a dash', () => {
    expect(formatDateTime(null)).toBe('—');
  });
  it('renders invalid input as a dash', () => {
    expect(formatDateTime('not-a-date')).toBe('—');
  });
  it('renders a valid ISO timestamp as a non-empty string', () => {
    const out = formatDateTime('2026-07-12T14:30:00+00:00');
    expect(out).not.toBe('—');
    expect(out.length).toBeGreaterThan(0);
  });
});

describe('formatDuration', () => {
  it('scales seconds through days', () => {
    expect(formatDuration(45)).toBe('45s');
    expect(formatDuration(120)).toBe('2m');
    expect(formatDuration(7200)).toBe('2h');
    expect(formatDuration(172800)).toBe('2d');
  });
});

describe('timeUntil', () => {
  const now = new Date('2026-07-09T12:00:00Z');
  it('returns now only for the current minute', () => {
    expect(timeUntil('2026-07-09T12:00:00Z', now)).toBe('now');
    expect(timeUntil('2026-07-09T11:59:30Z', now)).toBe('now');
  });
  it('returns an explicit ago form for past times', () => {
    expect(timeUntil('2026-07-09T11:00:00Z', now)).toBe('1h ago');
    expect(timeUntil('2026-07-08T20:38:00Z', now)).toBe('15h ago');
  });
  it('returns minutes and hours for the future', () => {
    expect(timeUntil('2026-07-09T12:43:00Z', now)).toBe('in 43m');
    expect(timeUntil('2026-07-09T14:30:00Z', now)).toBe('in 2h 30m');
  });
  it('handles null', () => {
    expect(timeUntil(null)).toBe('—');
  });
});

describe('timeAgo', () => {
  const now = new Date('2026-07-09T12:00:00Z');
  it('formats past times compactly', () => {
    expect(timeAgo('2026-07-09T11:45:00Z', now)).toBe('15m ago');
    expect(timeAgo('2026-07-09T10:00:00Z', now)).toBe('2h ago');
    expect(timeAgo('2026-07-07T12:00:00Z', now)).toBe('2d ago');
    expect(timeAgo('2026-07-09T11:59:50Z', now)).toBe('just now');
  });
});

describe('utilizationStatus', () => {
  it('maps thresholds', () => {
    expect(utilizationStatus(10)).toBe('good');
    expect(utilizationStatus(85)).toBe('warning');
    expect(utilizationStatus(96)).toBe('critical');
  });
});

describe('windowLabel', () => {
  it('maps known windows and passes through unknown', () => {
    expect(windowLabel('five_hour')).toBe('5-hour block');
    expect(windowLabel('mystery')).toBe('mystery');
  });
});

describe('modelLabel', () => {
  it('humanizes dated and undated claude ids', () => {
    expect(modelLabel('claude-opus-4-8-20260101')).toBe('Opus 4.8');
    expect(modelLabel('claude-sonnet-4-6')).toBe('Sonnet 4.6');
    expect(modelLabel('claude-haiku-4-5-20251001')).toBe('Haiku 4.5');
    expect(modelLabel('claude-fable-5')).toBe('Fable 5');
  });
  it('strips provider and bedrock suffixes', () => {
    expect(modelLabel('us.anthropic.claude-opus-4-5-20251101-v1:0')).toBe(
      'Opus 4.5'
    );
  });
  it('humanizes the legacy 3.x version-first family order', () => {
    expect(modelLabel('claude-3-7-sonnet-20250219')).toBe('Sonnet 3.7');
    expect(modelLabel('claude-3-haiku-20240307')).toBe('Haiku 3');
    expect(modelLabel('claude-3-opus-20240229')).toBe('Opus 3');
  });
  it('passes through unrecognized ids', () => {
    expect(modelLabel('gpt-5-turbo-preview')).toBe('gpt-5-turbo-preview');
  });
});

describe('cacheReadShare', () => {
  it('computes the cache-read fraction', () => {
    expect(
      cacheReadShare([
        { cache_read_tokens: 900, total_tokens: 1000 },
        { cache_read_tokens: 0, total_tokens: 1000 },
      ])
    ).toBeCloseTo(0.45);
  });
  it('is zero for no usage', () => {
    expect(cacheReadShare([])).toBe(0);
  });
});
