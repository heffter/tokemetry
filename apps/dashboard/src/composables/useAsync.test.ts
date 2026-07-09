import { describe, expect, it } from 'vitest';
import { ApiError } from '@/api/client';
import { useAsync } from './useAsync';

describe('useAsync', () => {
  it('clears error and toggles loading around a successful run', async () => {
    const { loading, error, run } = useAsync();
    let seenLoading = false;
    await run(async () => {
      seenLoading = loading.value;
    });
    expect(seenLoading).toBe(true);
    expect(loading.value).toBe(false);
    expect(error.value).toBeNull();
  });

  it('captures a human error message on failure', async () => {
    const { error, loading, run } = useAsync();
    await run(async () => {
      throw new ApiError(401, 'x');
    });
    expect(error.value).toContain('token');
    expect(loading.value).toBe(false);
  });

  it('retry re-runs the last loader', async () => {
    const { run, retry } = useAsync();
    let calls = 0;
    await run(async () => {
      calls += 1;
    });
    retry();
    // retry runs asynchronously; allow the microtask to settle.
    await Promise.resolve();
    expect(calls).toBe(2);
  });
});
