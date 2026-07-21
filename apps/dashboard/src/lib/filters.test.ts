import { describe, expect, it } from 'vitest';
import {
  clampRangeDays,
  dayEndIso,
  dayStartIso,
  enumerateDays,
  isoDay,
  presetRange,
} from './filters';

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

describe('dayStartIso / dayEndIso', () => {
  it('maps a day to its start and inclusive end instants', () => {
    expect(dayStartIso('2026-07-21')).toBe('2026-07-21T00:00:00Z');
    expect(dayEndIso('2026-07-21')).toBe('2026-07-21T23:59:59.999Z');
  });

  it('end bound includes an event later on the same day', () => {
    // A snapshot stamped midday must fall within [start, end] of its own day;
    // a start-of-day upper bound would wrongly exclude it.
    const snapshot = new Date('2026-07-21T12:05:01Z').getTime();
    expect(new Date(dayStartIso('2026-07-21')).getTime()).toBeLessThanOrEqual(
      snapshot
    );
    expect(new Date(dayEndIso('2026-07-21')).getTime()).toBeGreaterThanOrEqual(
      snapshot
    );
  });

  it('a 365-day span with an end-of-day bound stays under 366 days', () => {
    const from = new Date(dayStartIso('2025-07-21')).getTime();
    const to = new Date(dayEndIso('2026-07-21')).getTime();
    const days = (to - from) / 86_400_000;
    expect(days).toBeGreaterThan(365);
    expect(days).toBeLessThanOrEqual(366);
  });
});
