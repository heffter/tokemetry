// A tiny async-state helper: track loading and a human error message for a
// loader, and remember it so a Retry button can re-run it.

import { ref } from 'vue';
import { errorMessage } from '@/lib/errors';

export function useAsync() {
  const loading = ref(false);
  const error = ref<string | null>(null);
  let lastFn: (() => Promise<void>) | null = null;

  async function run(fn: () => Promise<void>): Promise<void> {
    lastFn = fn;
    loading.value = true;
    error.value = null;
    try {
      await fn();
    } catch (e) {
      error.value = errorMessage(e);
    } finally {
      loading.value = false;
    }
  }

  function retry(): void {
    if (lastFn) void run(lastFn);
  }

  return { loading, error, run, retry };
}
