import { describe, expect, it } from 'vitest';
import { isoDay, presetRange } from './filters';

describe('presetRange', () => {
  const now = new Date('2026-07-09T12:00:00Z');

  it('today is a single inclusive day', () => {
    expect(presetRange('today', now)).toEqual({
      from: '2026-07-09',
      to: '2026-07-09',
    });
  });

  it('7d spans the trailing week inclusive', () => {
    expect(presetRange('7d', now)).toEqual({
      from: '2026-07-03',
      to: '2026-07-09',
    });
  });

  it('30d and 90d go back the right number of days', () => {
    expect(presetRange('30d', now).from).toBe('2026-06-10');
    expect(presetRange('90d', now).from).toBe('2026-04-11');
  });

  it('all starts before any usage', () => {
    expect(presetRange('all', now)).toEqual({
      from: '2020-01-01',
      to: '2026-07-09',
    });
  });
});

describe('isoDay', () => {
  it('formats the UTC calendar day', () => {
    expect(isoDay(new Date('2026-01-02T23:30:00Z'))).toBe('2026-01-02');
  });
});
