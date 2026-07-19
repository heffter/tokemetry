import { describe, expect, it } from 'vitest';
import { sourceFlags, supportedSchemaVersion } from './sources';
import type { SourceV2 } from '@/api/types-v2';

function source(
  health: Partial<SourceV2['health']> = {},
  overrides: Partial<SourceV2> = {}
): SourceV2 {
  return {
    id: 1,
    type: 'collector',
    name: 'box-1',
    version: '1.0.0',
    instance_id: null,
    machine: 'box-1',
    token_label: null,
    billing_mode: 'subscription',
    first_seen: '2026-07-01T00:00:00Z',
    last_seen: '2026-07-19T00:00:00Z',
    revoked: false,
    health: {
      stale: false,
      last_successful_ingest: '2026-07-19T00:00:00Z',
      recent_error_count: 0,
      reported_schema_version: 3,
      clock_skew_seconds: 0,
      staleness_threshold_seconds: 1800,
      ...health,
    },
    ...overrides,
  };
}

describe('supportedSchemaVersion', () => {
  it('is the max reported version across sources', () => {
    expect(
      supportedSchemaVersion([
        source({ reported_schema_version: 2 }),
        source({ reported_schema_version: 4 }),
        source({ reported_schema_version: null }),
      ])
    ).toBe(4);
  });

  it('is null when no source reports a version', () => {
    expect(
      supportedSchemaVersion([source({ reported_schema_version: null })])
    ).toBeNull();
  });
});

describe('sourceFlags', () => {
  it('flags a source reporting an older schema than the supported version', () => {
    const flags = sourceFlags(source({ reported_schema_version: 2 }), 4);
    expect(flags.schemaDrift).toBe(true);
  });

  it('does not flag drift when at the supported version', () => {
    expect(
      sourceFlags(source({ reported_schema_version: 4 }), 4).schemaDrift
    ).toBe(false);
  });

  it('passes through the server staleness flag', () => {
    expect(sourceFlags(source({ stale: true }), 3).stale).toBe(true);
  });

  it('flags a large clock skew in either direction', () => {
    expect(sourceFlags(source({ clock_skew_seconds: 120 }), 3).clockSkew).toBe(
      true
    );
    expect(sourceFlags(source({ clock_skew_seconds: -120 }), 3).clockSkew).toBe(
      true
    );
    expect(sourceFlags(source({ clock_skew_seconds: 5 }), 3).clockSkew).toBe(
      false
    );
  });
});
