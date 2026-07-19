import { describe, expect, it } from 'vitest';
import {
  CATEGORICAL_DARK,
  CATEGORICAL_LIGHT,
  keyColor,
  seriesColor,
  stableColorMap,
} from './palette';

describe('stableColorMap', () => {
  it('assigns the same color to a key regardless of input order', () => {
    const a = stableColorMap(['openai', 'anthropic', 'google'], false);
    const b = stableColorMap(['google', 'openai', 'anthropic'], false);
    for (const key of ['openai', 'anthropic', 'google']) {
      expect(a.get(key)).toBe(b.get(key));
    }
  });

  it('assigns colors by sorted position so a key is order-independent', () => {
    const map = stableColorMap(['openai', 'anthropic'], false);
    // Sorted: anthropic -> index 0, openai -> index 1.
    expect(map.get('anthropic')).toBe(seriesColor(0, false));
    expect(map.get('openai')).toBe(seriesColor(1, false));
  });

  it('deduplicates keys', () => {
    const map = stableColorMap(['anthropic', 'anthropic', 'openai'], false);
    expect(map.size).toBe(2);
  });

  it('picks the dark ramp when dark is set', () => {
    const map = stableColorMap(['anthropic'], true);
    expect(map.get('anthropic')).toBe(CATEGORICAL_DARK[0]);
    expect(map.get('anthropic')).not.toBe(CATEGORICAL_LIGHT[0]);
  });

  it('wraps around the ramp past its length', () => {
    const keys = Array.from({ length: CATEGORICAL_LIGHT.length + 1 }, (_, i) =>
      String(i).padStart(2, '0')
    );
    const map = stableColorMap(keys, false);
    // The (ramp.length)-th sorted key wraps back to the first hue.
    expect(map.get(keys[CATEGORICAL_LIGHT.length])).toBe(seriesColor(0, false));
  });
});

describe('keyColor', () => {
  it('returns the stable color for a key within its set', () => {
    const keys = ['openai', 'anthropic'];
    expect(keyColor('anthropic', keys, false)).toBe(
      stableColorMap(keys, false).get('anthropic')
    );
  });

  it('falls back to the first hue for an unknown key', () => {
    expect(keyColor('mystery', ['anthropic'], false)).toBe(
      seriesColor(0, false)
    );
  });
});
