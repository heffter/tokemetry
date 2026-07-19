import { describe, expect, it } from 'vitest';
import { clampRangeDays, enumerateDays, isoDay, presetRange } from './filters';

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

describe('enumerateDays', () => {
  it('lists every day inclusive, filling gaps', () => {
    expect(enumerateDays('2026-06-01', '2026-06-04')).toEqual([
      '2026-06-01',
      '2026-06-02',
      '2026-06-03',
      '2026-06-04',
    ]);
  });
  it('returns a single day when from equals to', () => {
    expect(enumerateDays('2026-06-01', '2026-06-01')).toEqual(['2026-06-01']);
  });
  it('returns empty for an inverted or invalid range', () => {
    expect(enumerateDays('2026-06-04', '2026-06-01')).toEqual([]);
    expect(enumerateDays('nope', '2026-06-01')).toEqual([]);
  });
});

describe('clampRangeDays', () => {
  it('leaves a range within the limit untouched', () => {
    const r = clampRangeDays('2026-06-01', '2026-06-30', 365);
    expect(r).toEqual({ from: '2026-06-01', to: '2026-06-30', clamped: false });
  });

  it('moves "from" forward to the last maxDays and flags it', () => {
    const r = clampRangeDays('2020-01-01', '2026-07-19', 365);
    expect(r.clamped).toBe(true);
    expect(r.to).toBe('2026-07-19');
    expect(r.from).toBe('2025-07-19'); // exactly 365 days before "to"
  });

  it('does not clamp an unparseable range', () => {
    const r = clampRangeDays('nope', '2026-07-19', 365);
    expect(r.clamped).toBe(false);
  });
});
