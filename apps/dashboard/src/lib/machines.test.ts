import { describe, expect, it } from 'vitest';
import { isDown, machineStatus, statusRank } from './machines';

const NOW = new Date('2026-07-09T12:00:00Z').getTime();
const ago = (min: number) => new Date(NOW - min * 60_000).toISOString();

describe('machineStatus', () => {
  it('classifies by elapsed time', () => {
    expect(machineStatus(ago(0.5), NOW).level).toBe('online');
    expect(machineStatus(ago(10), NOW).level).toBe('recent');
    expect(machineStatus(ago(90), NOW).level).toBe('stale');
    expect(machineStatus(ago(3000), NOW).level).toBe('offline');
    expect(machineStatus(null, NOW).level).toBe('offline');
  });
  it('formats time ago', () => {
    expect(machineStatus(ago(0.2), NOW).ago).toBe('just now');
    expect(machineStatus(ago(15), NOW).ago).toBe('15m ago');
    expect(machineStatus(ago(120), NOW).ago).toBe('2h ago');
    expect(machineStatus(null, NOW).ago).toBe('never');
  });
});

describe('statusRank and isDown', () => {
  it('ranks offline/stale first', () => {
    expect(statusRank('offline')).toBeLessThan(statusRank('online'));
    expect(statusRank('stale')).toBeLessThan(statusRank('recent'));
  });
  it('flags stale and offline as down', () => {
    expect(isDown('stale')).toBe(true);
    expect(isDown('offline')).toBe(true);
    expect(isDown('recent')).toBe(false);
  });
});
