// Cost aggregation for the dual-metric cost views (FR-COST-012, D-007).
//
// The two cost metrics -- actual API spend and subscription-equivalent value --
// are always kept as separate series and never summed into one total; a merged
// "cost" number would be meaningless across a subscription plan and a
// pay-per-token API. These helpers total a set of CostRowV2 rows (money arrives
// as decimal strings on the wire) and classify a row's pricing completeness for
// the unpriced/partial badge (FR-UI-008). Pure and unit-tested.

import type { CostRowV2 } from '@/api/types-v2';

/** Numeric totals over a cost-row set; the two metrics stay distinct. */
export interface CostTotals {
  actualSpend: number;
  subscriptionValue: number;
  priced: number;
  partial: number;
  estimated: number;
  unpricedEvents: number;
}

/** Sum each cost field across rows (parsing the decimal-string money fields). */
export function sumCostRows(rows: CostRowV2[]): CostTotals {
  const totals: CostTotals = {
    actualSpend: 0,
    subscriptionValue: 0,
    priced: 0,
    partial: 0,
    estimated: 0,
    unpricedEvents: 0,
  };
  for (const row of rows) {
    totals.actualSpend += Number(row.actual_spend_usd);
    totals.subscriptionValue += Number(row.subscription_value_usd);
    totals.priced += Number(row.cost_priced_usd);
    totals.partial += Number(row.cost_partial_usd);
    totals.estimated += Number(row.cost_estimated_usd);
    totals.unpricedEvents += row.unpriced_event_count;
  }
  return totals;
}

/** A row's pricing completeness for the cost-status badge. */
export type CostStatus = 'priced' | 'partial' | 'unpriced';

/**
 * Classify a cost row: ``unpriced`` when it has unpriced events and no priced
 * or partial cost at all; ``partial`` when some cost is missing or estimated;
 * otherwise ``priced``. Drives the badge that links to the data-quality page.
 */
export function costStatusOf(row: CostRowV2): CostStatus {
  const priced = Number(row.cost_priced_usd);
  const partial = Number(row.cost_partial_usd);
  const estimated = Number(row.cost_estimated_usd);
  if (row.unpriced_event_count > 0 && priced === 0 && partial === 0) {
    return 'unpriced';
  }
  if (row.unpriced_event_count > 0 || partial > 0 || estimated > 0) {
    return 'partial';
  }
  return 'priced';
}
