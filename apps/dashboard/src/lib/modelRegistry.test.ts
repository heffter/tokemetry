import { describe, expect, it } from 'vitest';
import { knownModelIds, resolveModel } from './modelRegistry';
import type { ModelV2 } from '@/api/types-v2';

function model(overrides: Partial<ModelV2> = {}): ModelV2 {
  return {
    provider: 'anthropic',
    native_model_id: 'claude-opus-4-5',
    lifecycle: 'active',
    capabilities: {},
    first_seen: null,
    last_seen: null,
    aliases: [],
    ...overrides,
  };
}

describe('knownModelIds', () => {
  it('collects native ids and their alias spellings', () => {
    const ids = knownModelIds([
      model({ native_model_id: 'claude-opus-4-5', aliases: ['opus-4.5'] }),
      model({ provider: 'openai', native_model_id: 'gpt-5', aliases: [] }),
    ]);
    expect(ids.has('claude-opus-4-5')).toBe(true);
    expect(ids.has('opus-4.5')).toBe(true);
    expect(ids.has('gpt-5')).toBe(true);
    expect(ids.has('unheard-of')).toBe(false);
  });
});

describe('resolveModel', () => {
  const known = knownModelIds([
    model({ native_model_id: 'claude-opus-4-8-20260101' }),
    model({ provider: 'openai', native_model_id: 'gpt-5-turbo' }),
  ]);

  it('humanizes a known Claude id and marks it known', () => {
    const resolved = resolveModel('claude-opus-4-8-20260101', known);
    expect(resolved.display).toBe('Opus 4.8');
    expect(resolved.native).toBe('claude-opus-4-8-20260101');
    expect(resolved.known).toBe(true);
  });

  it('passes a non-Claude id through as its own display and marks it known', () => {
    const resolved = resolveModel('gpt-5-turbo', known);
    // No Claude-specific munging for a foreign id.
    expect(resolved.display).toBe('gpt-5-turbo');
    expect(resolved.known).toBe(true);
  });

  it('flags an id absent from the registry as unknown but still renders it', () => {
    const resolved = resolveModel('mystery-model-9', known);
    expect(resolved.known).toBe(false);
    expect(resolved.native).toBe('mystery-model-9');
    expect(resolved.display).toBe('mystery-model-9');
  });
});
