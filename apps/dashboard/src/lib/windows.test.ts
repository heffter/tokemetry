import { describe, expect, it } from 'vitest';
import { windowLabelsFrom } from './windows';
import { windowLabel } from './format';
import type { ProviderV2 } from '@/api/types-v2';

function provider(overrides: Partial<ProviderV2> = {}): ProviderV2 {
  return {
    id: 'anthropic',
    display_name: 'Anthropic',
    aliases: [],
    pricing_strategy: 'anthropic',
    limit_semantics: 'anthropic_oauth_windows',
    supported_dimensions: [],
    windows: [],
    registered: true,
    ...overrides,
  };
}

const ANTHROPIC = provider({
  windows: [
    {
      kind: 'five_hour',
      label: '5-hour block',
      period_kind: 'rolling',
      period_seconds: 18000,
      sort_order: 0,
    },
    {
      kind: 'seven_day',
      label: 'Weekly',
      period_kind: 'rolling',
      period_seconds: 604800,
      sort_order: 1,
    },
    {
      kind: 'seven_day_opus',
      label: 'Weekly (Opus)',
      period_kind: 'rolling',
      period_seconds: 604800,
      sort_order: 2,
    },
    {
      kind: 'seven_day_sonnet',
      label: 'Weekly (Sonnet)',
      period_kind: 'rolling',
      period_seconds: 604800,
      sort_order: 3,
    },
  ],
});

describe('windowLabelsFrom', () => {
  it('flattens provider window descriptors into a kind -> label map', () => {
    const map = windowLabelsFrom([ANTHROPIC]);
    expect(map).toEqual({
      five_hour: '5-hour block',
      seven_day: 'Weekly',
      seven_day_opus: 'Weekly (Opus)',
      seven_day_sonnet: 'Weekly (Sonnet)',
    });
  });

  it('is empty for providers with no windows', () => {
    expect(windowLabelsFrom([provider({ windows: [] })])).toEqual({});
  });
});

describe('registry-driven windowLabel (zero visual change contract)', () => {
  it('resolves every seeded Anthropic kind to its exact hardcoded label', () => {
    // Contract for FR-LIMIT-012 / FR-UI-014: the registry-sourced labels must
    // equal the labels the dashboard rendered before the registry existed.
    const map = windowLabelsFrom([ANTHROPIC]);
    expect(windowLabel('five_hour', map)).toBe('5-hour block');
    expect(windowLabel('seven_day', map)).toBe('Weekly');
    expect(windowLabel('seven_day_opus', map)).toBe('Weekly (Opus)');
    expect(windowLabel('seven_day_sonnet', map)).toBe('Weekly (Sonnet)');
    // A provider window with no descriptor still falls back to the raw kind.
    expect(windowLabel('rpm', map)).toBe('rpm');
  });
});
