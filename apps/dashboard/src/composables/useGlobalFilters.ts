// Cross-view provider and model filters (FR-UI-002).
//
// A single app-wide selection of provider and model that every view applies to
// its v2 queries, so switching provider on one page carries to the next. State
// is module-scoped (a singleton, like useSettings/useApi), persisted in
// localStorage, and mirrored to the URL query so a filtered view is shareable.
//
// Selection is single-value per dimension because the v2 query API filters take
// a single provider and a single model (see api/client.ts::V2Filters); an empty
// string means "all". The URL/localStorage plumbing here is framework-light and
// pure so it is unit-tested without a router; a component wires it to
// vue-router via useFilterRouteSync().

import { ref } from 'vue';
import type { V2Filters } from '@/api/client';

const PROVIDER_KEY = 'tokemetry.filter.provider';
const MODEL_KEY = 'tokemetry.filter.model';

/** Read a persisted filter value, tolerating unavailable storage. */
function readStored(key: string): string {
  try {
    return localStorage.getItem(key) ?? '';
  } catch {
    return '';
  }
}

/** Persist (or clear, when empty) a filter value, tolerating storage errors. */
function writeStored(key: string, value: string): void {
  try {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
  } catch {
    // Storage unavailable (private mode); the selection just won't persist.
  }
}

const provider = ref<string>(readStored(PROVIDER_KEY));
const model = ref<string>(readStored(MODEL_KEY));

/** The query keys this store owns in the URL, so route sync can round-trip them. */
export const FILTER_QUERY_KEYS = ['provider', 'model'] as const;

/**
 * The global provider/model filter, its setters, and (de)serialization helpers.
 *
 * ``setProvider`` clears the model when the provider changes, since a model
 * belongs to one provider and the prior selection would no longer be valid.
 */
export function useGlobalFilters() {
  function setProvider(value: string): void {
    if (value !== provider.value) {
      provider.value = value;
      writeStored(PROVIDER_KEY, value);
      setModel('');
    }
  }

  function setModel(value: string): void {
    model.value = value;
    writeStored(MODEL_KEY, value);
  }

  /** Clear both selections (back to "all"). */
  function clear(): void {
    setProvider('');
  }

  /** The selection as v2 query filters, omitting empty ("all") dimensions. */
  function filtersForApi(): V2Filters {
    const filters: V2Filters = {};
    if (provider.value) filters.provider = provider.value;
    if (model.value) filters.model = model.value;
    return filters;
  }

  /** The selection as a URL query fragment, omitting empty dimensions. */
  function toQuery(): Record<string, string> {
    const query: Record<string, string> = {};
    if (provider.value) query.provider = provider.value;
    if (model.value) query.model = model.value;
    return query;
  }

  /**
   * Adopt provider/model from a URL query (e.g. on load or route change).
   *
   * Absent keys reset to "all" so the store always reflects the URL. Assigns
   * directly rather than via setProvider so an explicit model in the same query
   * is not wiped by the provider change.
   */
  function applyFromQuery(query: Record<string, string | undefined>): void {
    const nextProvider = query.provider ?? '';
    const nextModel = query.model ?? '';
    if (nextProvider !== provider.value) {
      provider.value = nextProvider;
      writeStored(PROVIDER_KEY, nextProvider);
    }
    if (nextModel !== model.value) {
      model.value = nextModel;
      writeStored(MODEL_KEY, nextModel);
    }
  }

  return {
    provider,
    model,
    setProvider,
    setModel,
    clear,
    filtersForApi,
    toQuery,
    applyFromQuery,
  };
}
