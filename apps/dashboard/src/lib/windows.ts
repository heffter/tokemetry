// Registry-driven limit-window labels (FR-LIMIT-012).
//
// The provider registry (GET /api/v2/providers) now declares each provider's
// window kinds and their display labels. windowLabelsFrom flattens those into a
// kind -> label map that windowLabel(kind, map) consumes, so the dashboard
// resolves window labels dynamically instead of hardcoding five_hour/seven_day.
// Anthropic's seeded labels equal the old hardcoded ones, so this is a
// zero-visual-change swap; a provider window with no descriptor falls back to
// the raw kind. Pure and unit-tested.

import type { ProviderV2 } from '@/api/types-v2';

/**
 * Build a ``window_kind -> label`` map from every provider's window
 * descriptors. A later provider wins on a kind collision (kinds are
 * provider-defined and usually unique).
 */
export function windowLabelsFrom(
  providers: ProviderV2[]
): Record<string, string> {
  const labels: Record<string, string> = {};
  for (const provider of providers) {
    for (const window of provider.windows) {
      labels[window.kind] = window.label;
    }
  }
  return labels;
}
