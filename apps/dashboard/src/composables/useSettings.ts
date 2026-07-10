// User display preferences persisted in localStorage. The timezone drives
// every absolute date/time render (see lib/format.ts::formatDateTime); relative
// displays ("in 2h", "3m ago") are timezone-independent and unaffected.

import { ref } from 'vue';

const TIMEZONE_KEY = 'tokemetry.timezone';

/** The browser's IANA timezone, used as the default ("auto"). */
export function browserTimezone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

/** The full IANA timezone list, or a small fallback on older engines. */
export function availableTimezones(): string[] {
  const supported = Intl.supportedValuesOf;
  if (typeof supported === 'function') return supported('timeZone');
  return ['UTC', browserTimezone()];
}

// '' means "Auto (browser)"; resolved lazily so a changed browser zone is
// honored without the user re-picking.
const timezonePref = ref<string>(localStorage.getItem(TIMEZONE_KEY) ?? '');

/** The effective IANA timezone (browser default when the preference is auto). */
export function activeTimezone(): string {
  return timezonePref.value || browserTimezone();
}

/** Reactive timezone preference plus a setter ('' selects auto/browser). */
export function useSettings() {
  function setTimezone(value: string): void {
    timezonePref.value = value;
    if (value) localStorage.setItem(TIMEZONE_KEY, value);
    else localStorage.removeItem(TIMEZONE_KEY);
  }
  return { timezonePref, setTimezone, activeTimezone, browserTimezone };
}
