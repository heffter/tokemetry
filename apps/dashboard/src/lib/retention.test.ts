import { describe, expect, it } from 'vitest';
import type { RetentionStatusResponse } from '@/api/client';
import { retentionPolicyLabel, summarizeRetention } from './retention';

function status(): RetentionStatusResponse {
  return {
    legal_hold: false,
    categories: [
      {
        category: 'raw_events',
        retention_days: 180,
        enabled: true,
        last_run_at: '2026-07-21T00:00:00Z',
        last_deleted: 5,
        total_deleted: 20,
        pending_backlog: 3,
        oldest_retained: '2026-01-01T00:00:00Z',
      },
      {
        category: 'daily_rollups',
        retention_days: null,
        enabled: true,
        last_run_at: null,
        last_deleted: 0,
        total_deleted: 0,
        pending_backlog: 0,
        oldest_retained: null,
      },
      {
        category: 'audit_records',
        retention_days: 400,
        enabled: true,
        last_run_at: null,
        last_deleted: 0,
        total_deleted: 0,
        pending_backlog: 0,
        oldest_retained: null,
      },
    ],
  };
}

describe('summarizeRetention', () => {
  it('sums deleted and backlog across categories', () => {
    const s = summarizeRetention(status());
    expect(s.totalDeleted).toBe(20);
    expect(s.totalBacklog).toBe(3);
    expect(s.categoriesWithBacklog).toEqual(['raw_events']);
  });

  it('flags enabled finite categories that never ran', () => {
    // audit_records is enabled + finite but never ran; daily_rollups is
    // indefinite so it is not expected to run.
    const s = summarizeRetention(status());
    expect(s.neverRun).toEqual(['audit_records']);
  });

  it('passes through the legal hold', () => {
    const held = status();
    held.legal_hold = true;
    expect(summarizeRetention(held).legalHold).toBe(true);
  });
});

describe('retentionPolicyLabel', () => {
  it('labels finite, indefinite, and disabled categories', () => {
    const [raw, rollups] = status().categories;
    expect(retentionPolicyLabel(raw)).toBe('180d');
    expect(retentionPolicyLabel(rollups)).toBe('indefinite');
    expect(retentionPolicyLabel({ ...raw, enabled: false })).toBe('disabled');
  });
});
