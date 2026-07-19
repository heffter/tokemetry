import { describe, expect, it } from 'vitest';
import { costStatusOf, sumCostRows } from './costs';
import type { CostRowV2 } from '@/api/types-v2';

function costRow(overrides: Partial<CostRowV2> = {}): CostRowV2 {
  return {
    key: 'anthropic',
    actual_spend_usd: '0',
    subscription_value_usd: '0',
    cost_priced_usd: '0',
    cost_partial_usd: '0',
    cost_estimated_usd: '0',
    unpriced_event_count: 0,
    pricing_version: '1',
    ...overrides,
  };
}

describe('sumCostRows', () => {
  it('totals the metrics separately and never merges them', () => {
    const totals = sumCostRows([
      costRow({ actual_spend_usd: '1.50', subscription_value_usd: '10.00' }),
      costRow({ actual_spend_usd: '2.50', subscription_value_usd: '20.00' }),
    ]);
    expect(totals.actualSpend).toBeCloseTo(4.0);
    expect(totals.subscriptionValue).toBeCloseTo(30.0);
    // The two are distinct fields; nothing sums spend into value.
    expect(totals.actualSpend).not.toBe(totals.subscriptionValue);
  });

  it('parses the decimal-string wire form, including scientific-notation zero', () => {
    const totals = sumCostRows([
      costRow({ actual_spend_usd: '0E-10', cost_priced_usd: '0E-10' }),
    ]);
    expect(totals.actualSpend).toBe(0);
    expect(totals.priced).toBe(0);
  });

  it('accumulates unpriced event counts', () => {
    const totals = sumCostRows([
      costRow({ unpriced_event_count: 3 }),
      costRow({ unpriced_event_count: 2 }),
    ]);
    expect(totals.unpricedEvents).toBe(5);
  });
});

describe('costStatusOf', () => {
  it('is priced when fully priced with no unpriced events', () => {
    expect(costStatusOf(costRow({ cost_priced_usd: '5.00' }))).toBe('priced');
  });

  it('is unpriced when all events are unpriced', () => {
    expect(
      costStatusOf(costRow({ unpriced_event_count: 4, cost_priced_usd: '0' }))
    ).toBe('unpriced');
  });

  it('is partial when some cost is priced but events remain unpriced', () => {
    expect(
      costStatusOf(
        costRow({ unpriced_event_count: 1, cost_priced_usd: '5.00' })
      )
    ).toBe('partial');
  });

  it('is partial when any cost is estimated', () => {
    expect(costStatusOf(costRow({ cost_estimated_usd: '2.00' }))).toBe(
      'partial'
    );
  });
});
