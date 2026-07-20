import { describe, expect, it } from 'vitest';
import {
  ALERT_KINDS,
  buildAlertConfig,
  buildFilters,
  FILTER_DIMENSIONS,
  parseFilterValues,
} from './alerts';

describe('ALERT_KINDS catalog', () => {
  it('covers every server rule kind', () => {
    // Kept in sync with services/alerting/rules.py EVALUATORS +
    // GROUPED_EVALUATOR_KINDS.
    const expected = [
      'limit_pct',
      'burn_rate',
      'predicted_exhaustion',
      'collector_stale',
      'stale_source',
      'unpriced_events',
      'unknown_model',
      'failure_rate',
      'latency_p95',
      'fallback_rate',
      'schema_drift',
    ].sort();
    expect(Object.keys(ALERT_KINDS).sort()).toEqual(expected);
  });

  it('gives threshold kinds warn/crit defaults with warn <= crit', () => {
    for (const [kind, meta] of Object.entries(ALERT_KINDS)) {
      if (!meta.threshold) continue;
      const warn = Number(meta.threshold.warnDef);
      const crit = Number(meta.threshold.critDef);
      expect(Number.isNaN(warn), kind).toBe(false);
      expect(Number.isNaN(crit), kind).toBe(false);
      expect(warn, kind).toBeLessThanOrEqual(crit);
    }
  });

  it('marks state-based kinds as threshold-free', () => {
    expect(ALERT_KINDS.stale_source.threshold).toBeNull();
    expect(ALERT_KINDS.schema_drift.threshold).toBeNull();
    expect(ALERT_KINDS.predicted_exhaustion.threshold).toBeNull();
  });

  it('marks only limit_pct as window-bearing', () => {
    const windowed = Object.entries(ALERT_KINDS)
      .filter(([, m]) => m.window)
      .map(([k]) => k);
    expect(windowed).toEqual(['limit_pct']);
  });
});

describe('parseFilterValues', () => {
  it('splits, trims, and drops empties', () => {
    expect(parseFilterValues(' anthropic , openai ,')).toEqual([
      'anthropic',
      'openai',
    ]);
  });

  it('de-duplicates repeated values', () => {
    expect(parseFilterValues('a,a,b')).toEqual(['a', 'b']);
  });

  it('returns an empty list for blank input', () => {
    expect(parseFilterValues('   ')).toEqual([]);
  });
});

describe('buildFilters', () => {
  it('includes only dimensions with values', () => {
    const filters = buildFilters({
      provider: 'anthropic',
      model: '',
      source: 'proxy-a',
    });
    expect(filters).toEqual({ provider: ['anthropic'], source: ['proxy-a'] });
  });

  it('returns null when nothing is scoped', () => {
    expect(buildFilters({ provider: '', model: ' ' })).toBeNull();
    expect(buildFilters({})).toBeNull();
  });

  it('covers every declared dimension', () => {
    const inputs = Object.fromEntries(
      FILTER_DIMENSIONS.map((d) => [d, d])
    ) as Record<string, string>;
    const filters = buildFilters(inputs);
    for (const dim of FILTER_DIMENSIONS) {
      expect(filters?.[dim]).toEqual([dim]);
    }
  });
});

describe('buildAlertConfig', () => {
  it('wraps filters when present', () => {
    expect(buildAlertConfig({ provider: 'anthropic' })).toEqual({
      filters: { provider: ['anthropic'] },
    });
  });

  it('returns undefined when unscoped (config omitted)', () => {
    expect(buildAlertConfig({})).toBeUndefined();
    expect(buildAlertConfig({ provider: '' })).toBeUndefined();
  });
});
