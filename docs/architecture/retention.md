# Data retention

Retention is **configurable per record category** (Epic TOK-10, PRD 12.18).
Each category has a duration in days -- or is kept indefinitely -- and can be
individually enabled or disabled. A single global **legal hold** suspends all
deletion at once.

> Enforcement (the incremental, resumable retention worker that actually
> deletes eligible rows, verifying rollups before removing raw events) lands in
> Task 70.2. Task 70.1 delivers the policy model and its administration API;
> until the worker ships, the policy is defined but no rows are deleted.

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
