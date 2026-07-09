import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { throttle } from './throttle';

describe('throttle', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('runs immediately on the leading edge', () => {
    const fn = vi.fn();
    throttle(fn, 100)();
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('coalesces a burst into one leading + one trailing call', () => {
    const fn = vi.fn();
    const t = throttle(fn, 100);
    t('a');
    t('b');
    t('c');
    expect(fn).toHaveBeenCalledTimes(1);
    expect(fn).toHaveBeenLastCalledWith('a');

    vi.advanceTimersByTime(100);
    expect(fn).toHaveBeenCalledTimes(2);
    expect(fn).toHaveBeenLastCalledWith('c'); // latest args, not dropped
  });

  it('allows another leading call after the window elapses', () => {
    const fn = vi.fn();
    const t = throttle(fn, 100);
    t();
    vi.advanceTimersByTime(150);
    t();
    expect(fn).toHaveBeenCalledTimes(2);
  });
});
