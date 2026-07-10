// Trailing-edge throttle: run at most once per `ms`, with a guaranteed
// trailing call so the latest invocation is never dropped. Used to coalesce a
// burst of WebSocket events into a single refetch instead of one per event.

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function throttle<T extends (...args: any[]) => void>(
  fn: T,
  ms: number
): (...args: Parameters<T>) => void {
  let last = Number.NEGATIVE_INFINITY;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let pending: Parameters<T> | null = null;

  return (...args: Parameters<T>): void => {
    pending = args;
    const now = Date.now();
    const remaining = ms - (now - last);
    if (remaining <= 0) {
      last = now;
      pending = null;
      fn(...args);
    } else if (timer === null) {
      timer = setTimeout(() => {
        last = Date.now();
        timer = null;
        if (pending !== null) {
          const call = pending;
          pending = null;
          fn(...call);
        }
      }, remaining);
    }
  };
}
