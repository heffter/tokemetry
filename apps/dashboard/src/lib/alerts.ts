// Alert rule-editor metadata and helpers, extracted from AlertsView so the
// kind catalog and filter parsing are unit-testable. Kept in sync with the
// server's alert kinds (services/alerting/rules.py EVALUATORS +
// GROUPED_EVALUATOR_KINDS) and config schema (api/schemas_alerts.py).
import type { AlertFilters, AlertRuleConfig } from '@/api/client';

export interface KindThreshold {
  min?: number;
  max?: number;
  suffix: string;
  warnDef: string;
  critDef: string;
}

export interface KindMeta {
  label: string;
  // Whether the kind selects a limit window (window_kind).
  window: boolean;
  // Dual warn/critical thresholds; null for kinds that fire on a state and use
  // server-side defaults (predicted_exhaustion, stale_source, schema_drift).
  threshold: KindThreshold | null;
}

// Every rule kind the editor can create, in display order.
export const ALERT_KINDS: Record<string, KindMeta> = {
  limit_pct: {
    label: 'Limit %',
    window: true,
    threshold: { min: 0, max: 100, suffix: '%', warnDef: '80', critDef: '95' },
  },
  burn_rate: {
    label: 'Burn rate',
    window: false,
    threshold: { suffix: 'tok/min', warnDef: '5000', critDef: '10000' },
  },
  predicted_exhaustion: {
    label: 'Predicted exhaustion',
    window: false,
    threshold: null,
  },
  collector_stale: {
    label: 'Collector stale',
    window: false,
    threshold: { min: 0, suffix: 'min', warnDef: '30', critDef: '120' },
  },
  stale_source: { label: 'Stale source', window: false, threshold: null },
  unpriced_events: {
    label: 'Unpriced usage',
    window: false,
    threshold: { min: 0, suffix: 'events', warnDef: '1', critDef: '100' },
  },
  unknown_model: {
    label: 'Unknown model',
    window: false,
    threshold: { min: 0, suffix: 'events', warnDef: '1', critDef: '25' },
  },
  failure_rate: {
    label: 'Failure rate',
    window: false,
    threshold: { min: 0, max: 100, suffix: '%', warnDef: '10', critDef: '25' },
  },
  latency_p95: {
    label: 'Latency p95',
    window: false,
    threshold: { min: 0, suffix: 'ms', warnDef: '10000', critDef: '30000' },
  },
  fallback_rate: {
    label: 'Fallback rate',
    window: false,
    threshold: { min: 0, max: 100, suffix: '%', warnDef: '10', critDef: '25' },
  },
  schema_drift: { label: 'Schema drift', window: false, threshold: null },
};

// The dimension filters a rule may scope on, in stable order.
export const FILTER_DIMENSIONS = [
  'provider',
  'model',
  'source',
  'project',
  'environment',
] as const;

export type FilterDimension = (typeof FILTER_DIMENSIONS)[number];

// Split a comma-separated dimension input into a trimmed, de-duplicated,
// non-empty value list.
export function parseFilterValues(raw: string): string[] {
  const seen = new Set<string>();
  for (const part of raw.split(',')) {
    const value = part.trim();
    if (value) seen.add(value);
  }
  return [...seen];
}

// Build the AlertFilters object from the per-dimension text inputs, including
// only dimensions with at least one value. Returns null when nothing is scoped.
export function buildFilters(
  inputs: Partial<Record<FilterDimension, string>>
): AlertFilters | null {
  const filters: AlertFilters = {};
  let any = false;
  for (const dim of FILTER_DIMENSIONS) {
    const values = parseFilterValues(inputs[dim] ?? '');
    if (values.length > 0) {
      filters[dim] = values;
      any = true;
    }
  }
  return any ? filters : null;
}

// Build the rule config payload; returns undefined when there is nothing to
// send, so an unscoped rule omits config entirely (server default applies).
export function buildAlertConfig(
  inputs: Partial<Record<FilterDimension, string>>
): AlertRuleConfig | undefined {
  const filters = buildFilters(inputs);
  return filters ? { filters } : undefined;
}
