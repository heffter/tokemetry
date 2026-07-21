# Data retention

Retention is **configurable per record category** (Epic TOK-10, PRD 12.18).
Each category has a duration in days -- or is kept indefinitely -- and can be
individually enabled or disabled. A single global **legal hold** suspends all
deletion at once. A background worker enforces the policy (Task 70.2).

## Categories and defaults

The policy (`services/retention.py`, `DEFAULT_RETENTION_POLICY`) ships PRD
12.18 defaults:

| Category | Key | Default | Notes |
|---|---|---|---|
| Raw final attempt events | `raw_events` | 180 days | Never shorter than the rollup verification lag (2 days) |
| Superseded snapshot revisions | `superseded_snapshots` | 7 days | Decision D-010 |
| Daily rollups | `daily_rollups` | Indefinite | Derived cache, rebuildable from events |
| Limit snapshots | `limit_snapshots` | 400 days | |
| Ingest batch metadata | `ingest_batches` | 30 days | Operational ledger |
| Security audit records | `audit_records` | 400 days | |
| Administrative corrections | `corrections` | Indefinite | |
| Alert events | `alert_events` | 400 days | |
| Renamed v1 archive | `v1_archive` | Indefinite, **disabled** | `usage_events_v1_archive` (Task 62.10); retained until an operator opts in |

`Indefinite` (a `null` duration) means the category is never aged out. A
category with `enabled: false` is never deleted regardless of its duration.

## Configuration

PRD defaults live in code. Operators override them at runtime through the
`app_settings` KV table under `retention.*` keys, written only via the admin
API -- a non-empty stored value wins over the default, a blank value falls back
to it (the same layering as channel config).

- `GET /api/v2/admin/retention` -- return the resolved policy (defaults plus
  overrides).
- `PUT /api/v2/admin/retention` -- validate and persist a full policy.

Both require the `admin:retention` token scope, and every `PUT` writes an
`audit_log` entry (`action = retention_policy_update`) recording the new policy
as content-free metadata.

**Validation** rejects nonsensical policies before any write: a finite duration
must be at least one day, every category must be present exactly once, and raw
events must be retained at least as long as the rollup verification lag so the
worker can never delete raw rows the rollup pipeline has not yet confirmed
(FR-RET-004).

## Legal hold

`legal_hold: true` suspends deletion across every category at once
(FR-RET-006), leaving durations untouched so normal retention resumes when the
hold is cleared.

## The retention worker

`services/retention_worker.py` runs a background sweep (disabled by default;
`TOKEMETRY_RETENTION_WORKER_ENABLED=true`, interval
`TOKEMETRY_RETENTION_WORKER_INTERVAL_SECONDS`, default hourly). Each sweep, for
every deletion-active category, deletes up to
`TOKEMETRY_RETENTION_WORKER_BATCH_SIZE` (default 5000) rows oldest-first. Because
deletion is destructive, an interrupted sweep simply resumes on the next tick --
deleted rows never reappear, so no rescanning is needed (FR-RET-002).

- **Raw events** are deleted a whole day at a time, and only after the covering
  daily rollups are verified to exist and match that day's event token sums
  (FR-RET-004, FR-ROLLUP-010). A mismatch aborts that day and records a
  `retention_rollup_mismatch` data-quality event; the day is retried on the next
  sweep once the rollups are corrected.
- **Referential integrity**: an event's `computed_costs` and `billable_units`
  rows are deleted before the event (FR-RET-003).
- **Superseded snapshots** (7 days) and **administrative corrections**
  (indefinite) are both `usage_event_revisions`, split by `reason`.
- Day windows are computed in Python (no dialect-specific date functions), so
  behaviour is identical on SQLite and Postgres (FR-RET-007). After a large
  Postgres deletion, run `VACUUM` (or rely on autovacuum) to reclaim space.

**Status** (FR-RET-005): each sweep upserts a `retention_status` row per
category (last run, rows deleted last time and cumulatively, current backlog,
oldest row still retained), surfaced at `GET /api/v2/admin/retention/status`
(scope `admin:retention`).

## Administrative deletion

`POST /api/v2/admin/data` (scope `admin:retention`) is the privacy-owner surface
for targeted, GDPR-style erasure and mistake recovery (FR-PRIV-007). Deletion is
scoped by any combination of `source`, `machine`, `project`, and a `start`/`end`
time range (all ANDed; at least one is required).

It is a two-step dry-run/confirm flow, mirroring the pricing import:

1. **Dry run** (`?dry_run=true`, the default) returns per-table counts
   (`usage_events_v2`, `computed_costs`, `billable_units`,
   `usage_event_revisions`) and a content `digest`, without touching data.
2. **Confirm** (`?dry_run=false`) must echo that `digest`. It is rejected (409)
   if the data changed since the dry run, or if a legal hold is active. On
   success it deletes dependents before events, and -- unless
   `recompute_rollups: false` -- drops and rebuilds the affected days' rollups
   so no stale grain lingers. Every execution writes an `audit_log` entry
   (`action = admin_data_delete`) with the actor, criteria, digest, and
   per-table counts (FR-PRIV-009).

## Audit trail

Every administrative mutation is recorded through one shared writer
(`services/audit.py` `record()`) into the append-only `audit_log`: repricing and
reverts, price imports and rate-card changes, retention-policy edits, targeted
deletions, and token create/revoke. Each entry holds the actor (token label or
bootstrap), action, subject, a content-free JSON `detail` (filters, counts,
versions -- never secrets or usage content, NFR-SEC-005), the timestamp, and the
correlating `request_id` when the action came through HTTP.

The log is append-only: there is no delete API, and it ages out only under the
`audit_records` retention category (400-day default). `GET /api/v2/admin/audit`
(scope `admin:retention`) reviews it, newest first, filterable by `action` and
`actor`.

## Deletions that already exist (unchanged)

None of these are part of the retention policy:

- **Alert rules/events** -- removed when a user deletes a rule
  (`DELETE /api/v1/alerts/{id}`), which cascades to that rule's events.
- **API tokens** -- soft-revoked on `DELETE /api/v1/tokens/{label}`.
- **Rollups** -- dropped and rebuilt by the admin rebuild endpoint, which
  regenerates them from the untouched events.
- **Migration downgrades** -- drop columns/tables, an operator action, never
  part of normal running.

## Backup pruning (files, not rows)

Separate from row retention, the nightly Postgres dump (`deploy/backup.sh`, run
by the `backup` service in `deploy/docker-compose.yml`) deletes dump *files*
older than `RETENTION_DAYS` (default 14). The live database it dumps from is
governed by the policy above.

## Privacy

Only metadata and token counts are stored; message content is never collected
(see [collector overview](../collector/overview.md)). Audit and retention
records likewise hold only counts and catalog identifiers, never usage content.
