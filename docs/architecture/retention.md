# Data retention

**Short answer: every usage event is kept forever.** tokemetry never prunes,
downsamples, or ages out usage data. The optimization report and every
historical breakdown can therefore rely on the full history being present.

## What is stored, and for how long

| Data | Table | Retention |
|---|---|---|
| Per-message usage events (tokens, model, session, project, machine, `is_sidechain`, timestamps) | `usage_events` | Forever — never pruned |
| Reconstructed sessions | `sessions` | Forever |
| Daily rollups (derived) | `daily_rollups` | Forever; rebuildable from events |
| Subscription limit snapshots | `limit_snapshots` | Forever |
| Machines | `machines` | Forever |
| Alert rules and fired events | `alert_rules`, `alert_events` | Until deleted via the API |
| API tokens | `api_tokens` | Until revoked |
| Channel settings | `app_settings` | Until cleared |

The raw `usage_events` rows are the source of truth. `daily_rollups` are a
derived cache and can be regenerated at any time from the events
(`POST /api/v1/admin/rebuild-rollups`), so they hold no unique history.

## Deletions that do exist

None of these touch usage history:

- **Alert rules/events** — removed only when a user deletes a rule
  (`DELETE /api/v1/alerts/{id}`), which cascades to that rule's events.
- **API tokens** — soft-revoked on `DELETE /api/v1/tokens/{label}`.
- **Rollups** — dropped and rebuilt by the admin rebuild endpoint; this
  regenerates them from the untouched events.
- **Migration downgrades** — drop columns/tables, an operator action, never
  part of normal running.

## Backup pruning (files, not rows)

The only time-based pruning in the system is on **backup files**, not database
rows. The nightly Postgres dump (`deploy/backup.sh`, run by the `backup`
service in `deploy/docker-compose.yml`) writes a compressed dump per night and
deletes dump *files* older than `RETENTION_DAYS` (default 14). The live
database it dumps from is never pruned.

## Implications

- **Growth is unbounded.** On a busy fleet the `usage_events` table grows
  without limit. This is intentional so long-range reports stay accurate. If
  storage ever becomes a concern, the right lever is to add an *explicit*,
  opt-in archival job (e.g. roll events older than N months into a coarser
  summary table) rather than silently dropping rows — no such job exists today.
- **Privacy.** Only metadata and token counts are stored; message content is
  never collected (see [collector overview](../collector/overview.md)). The
  Anthropic OAuth token and any credentials never leave the collector machine.
