// Source-health display flags (FR-SOURCE-006, FR-UI-009).
//
// The server computes each source's staleness; the dashboard adds two derived
// display flags: schema-version drift (a source reporting an older ingest schema
// than the newest any source reports) and a large clock skew. supportedSchema
// is derived client-side as the max reported version across sources, so a lone
// lagging source stands out without the dashboard hardcoding a schema number.
// Pure and unit-tested.

import type { SourceV2 } from '@/api/types-v2';

/** A clock skew beyond this many seconds is worth flagging. */
export const CLOCK_SKEW_THRESHOLD_S = 60;

/** Display flags for a source row. */
export interface SourceFlags {
  stale: boolean;
  schemaDrift: boolean;
  clockSkew: boolean;
}

/** The newest reported ingest-schema version across sources, or null if none report. */
export function supportedSchemaVersion(sources: SourceV2[]): number | null {
  let max: number | null = null;
  for (const source of sources) {
    const version = source.health.reported_schema_version;
    if (version !== null && (max === null || version > max)) max = version;
  }
  return max;
}

/** Derive a source's display flags against the supported schema version. */
export function sourceFlags(
  source: SourceV2,
  supportedSchema: number | null
): SourceFlags {
  const health = source.health;
  return {
    stale: health.stale,
    schemaDrift:
      supportedSchema !== null &&
      health.reported_schema_version !== null &&
      health.reported_schema_version < supportedSchema,
    clockSkew:
      health.clock_skew_seconds !== null &&
      Math.abs(health.clock_skew_seconds) > CLOCK_SKEW_THRESHOLD_S,
  };
}
