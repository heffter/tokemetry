// Retention-status display helpers, extracted from SettingsView so the
// summarization is unit-testable. Mirrors the server's
// GET /api/v2/admin/retention/status shape (Task 70.7).
import type {
  RetentionCategoryStatus,
  RetentionStatusResponse,
} from '@/api/client';

export interface RetentionSummary {
  legalHold: boolean;
  totalDeleted: number;
  totalBacklog: number;
  categoriesWithBacklog: string[];
  // Enabled, finite-retention categories that have never run a sweep -- a hint
  // the retention worker may be disabled.
  neverRun: string[];
}

// Derive the at-a-glance summary an operator sees from the raw status.
export function summarizeRetention(
  status: RetentionStatusResponse
): RetentionSummary {
  let totalDeleted = 0;
  let totalBacklog = 0;
  const categoriesWithBacklog: string[] = [];
  const neverRun: string[] = [];
  for (const category of status.categories) {
    totalDeleted += category.total_deleted;
    totalBacklog += category.pending_backlog;
    if (category.pending_backlog > 0) {
      categoriesWithBacklog.push(category.category);
    }
    const active = category.enabled && category.retention_days !== null;
    if (active && category.last_run_at === null) {
      neverRun.push(category.category);
    }
  }
  return {
    legalHold: status.legal_hold,
    totalDeleted,
    totalBacklog,
    categoriesWithBacklog,
    neverRun,
  };
}

// A short one-line label for a category's policy: its duration or "disabled".
export function retentionPolicyLabel(
  category: RetentionCategoryStatus
): string {
  if (!category.enabled) return 'disabled';
  return category.retention_days === null
    ? 'indefinite'
    : `${category.retention_days}d`;
}
