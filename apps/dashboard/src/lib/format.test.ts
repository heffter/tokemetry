import { describe, expect, it } from 'vitest';
import {
  formatCost,
  formatPct,
  formatTokens,
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
});

describe('formatPct', () => {
  it('formats to one decimal', () => {
    expect(formatPct(42.5)).toBe('42.5%');
  });
});

describe('timeUntil', () => {
  const now = new Date('2026-07-09T12:00:00Z');
  it('returns now for past times', () => {
    expect(timeUntil('2026-07-09T11:00:00Z', now)).toBe('now');
  });
  it('returns minutes and hours', () => {
    expect(timeUntil('2026-07-09T12:43:00Z', now)).toBe('in 43m');
    expect(timeUntil('2026-07-09T14:30:00Z', now)).toBe('in 2h 30m');
  });
  it('handles null', () => {
    expect(timeUntil(null)).toBe('—');
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
