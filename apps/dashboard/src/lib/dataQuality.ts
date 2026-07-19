// Data-quality feed helpers (FR-UI-008, US-010/012).
//
// Each data-quality event has a kind (unknown_provider, unknown_model,
// unpriced_event, sequence_conflict, schema_drift, limit_source_failure,
// clock_skew, ...). deepLinkFor maps the actionable kinds to the page where a
// user fixes them -- a pricing/registry problem goes to pricing admin, a
// pricing coverage gap goes to the cost view -- so the feed is a to-do list,
// not just a log. Pure and unit-tested.

/** A deep link to where an event's underlying problem is resolved. */
export interface DeepLink {
  to: string;
  label: string;
}

/** The fix-it destination for a data-quality event kind, or null if none. */
export function deepLinkFor(kind: string): DeepLink | null {
  if (kind.includes('unknown_provider') || kind.includes('unknown_model')) {
    return { to: '/pricing-admin', label: 'pricing admin' };
  }
  if (kind.includes('unpriced') || kind.includes('price')) {
    return { to: '/costs', label: 'costs' };
  }
  return null;
}
