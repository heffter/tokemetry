import { beforeEach, describe, expect, it } from 'vitest';
import { useGlobalFilters } from './useGlobalFilters';

const PROVIDER_KEY = 'tokemetry.filter.provider';
const MODEL_KEY = 'tokemetry.filter.model';

// The store is a module-scoped singleton, so reset it (and storage) before
// each test to keep them independent of import-time state and each other.
beforeEach(() => {
  localStorage.clear();
  useGlobalFilters().clear();
});

describe('useGlobalFilters', () => {
  it('persists provider and model selections to localStorage', () => {
    const { setProvider, setModel } = useGlobalFilters();
    setProvider('anthropic');
    setModel('claude-opus-4-5');
    expect(localStorage.getItem(PROVIDER_KEY)).toBe('anthropic');
    expect(localStorage.getItem(MODEL_KEY)).toBe('claude-opus-4-5');
  });

  it('clears the model when the provider changes', () => {
    const store = useGlobalFilters();
    store.setProvider('anthropic');
    store.setModel('claude-opus-4-5');
    store.setProvider('openai');
    expect(store.model.value).toBe('');
    expect(localStorage.getItem(MODEL_KEY)).toBeNull();
  });

  it('does not clear the model when the provider is re-selected unchanged', () => {
    const store = useGlobalFilters();
    store.setProvider('anthropic');
    store.setModel('claude-opus-4-5');
    store.setProvider('anthropic');
    expect(store.model.value).toBe('claude-opus-4-5');
  });

  it('clearing removes both dimensions from state and storage', () => {
    const store = useGlobalFilters();
    store.setProvider('anthropic');
    store.setModel('claude-opus-4-5');
    store.clear();
    expect(store.provider.value).toBe('');
    expect(store.model.value).toBe('');
    expect(localStorage.getItem(PROVIDER_KEY)).toBeNull();
    expect(localStorage.getItem(MODEL_KEY)).toBeNull();
  });

  it('filtersForApi omits empty ("all") dimensions', () => {
    const store = useGlobalFilters();
    expect(store.filtersForApi()).toEqual({});
    store.setProvider('anthropic');
    expect(store.filtersForApi()).toEqual({ provider: 'anthropic' });
    store.setModel('claude-opus-4-5');
    expect(store.filtersForApi()).toEqual({
      provider: 'anthropic',
      model: 'claude-opus-4-5',
    });
  });

  it('round-trips the selection through the URL query', () => {
    const store = useGlobalFilters();
    store.setProvider('anthropic');
    store.setModel('claude-opus-4-5');
    const query = store.toQuery();
    expect(query).toEqual({ provider: 'anthropic', model: 'claude-opus-4-5' });

    store.clear();
    store.applyFromQuery(query);
    expect(store.provider.value).toBe('anthropic');
    expect(store.model.value).toBe('claude-opus-4-5');
  });

  it('applyFromQuery keeps an explicit model when the provider also changes', () => {
    const store = useGlobalFilters();
    store.setProvider('anthropic');
    store.setModel('claude-opus-4-5');
    // A query naming both must not have the provider change wipe the model.
    store.applyFromQuery({ provider: 'openai', model: 'gpt-5' });
    expect(store.provider.value).toBe('openai');
    expect(store.model.value).toBe('gpt-5');
  });

  it('applyFromQuery resets dimensions absent from the query to "all"', () => {
    const store = useGlobalFilters();
    store.setProvider('anthropic');
    store.setModel('claude-opus-4-5');
    store.applyFromQuery({});
    expect(store.provider.value).toBe('');
    expect(store.model.value).toBe('');
  });
});
