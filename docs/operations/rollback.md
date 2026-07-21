# Rollback runbook

Reverting a bad migration or release (AC-018). There are two mechanisms:
**Alembic downgrade** (for reversible migrations) and **restore from backup**
(for everything else). Pick by what the migration did.

## Decide: downgrade or restore

1. Identify the migrations applied since the last known-good revision.
2. For each, read its `downgrade()`:
   - **Reversible** (adds a column/table/index, or a data change with an
     inverse): a downgrade is safe.
   - **Irreversible / lossy**: a downgrade cannot recover dropped data. The
     view swap (migration 0010, which renamed `usage_events` to
     `usage_events_v1_archive`) and any `DROP`/data-collapsing migration are in
     this class -- their downgrade may restore structure but not the exact
     prior rows.
3. If **all** intervening migrations are cleanly reversible, downgrade. If
   **any** is irreversible or the data is suspect, restore from backup.

## Downgrade path (reversible migrations)

1. `docker compose stop server`.
2. Downgrade to the target revision:
   `docker compose run --rm server alembic downgrade <revision>`.
3. Deploy the matching prior image (so the app code matches the schema).
4. Verify: `python -m tokemetry_server.ops.restore_verify <sync-url>` reports
   `at_head=true` for the *target* revision's head, and the dashboard renders.

## Restore path (irreversible migration or data loss)

1. `docker compose stop server`.
2. Restore the pre-migration backup:
   `deploy/restore.sh /backups/tokemetry-<pre-migration-stamp>.sql.gz`.
3. Confirm: `deploy/verify-restore.sh` exits 0.
4. Deploy the prior image and start the server.

Data written *after* the backup but before the rollback is lost -- this is why
the RPO is the backup interval. Communicate the window to affected users.

## What restore-from-backup covers

A restore returns the database to exactly the backed-up state: all tables, the
audit log, retention status, pricing, and tokens. It does **not** replay usage
that arrived after the backup; re-ingesting from collectors/proxies (which
retain their own recent history) fills the gap where possible, and idempotent
ingest means re-sending is safe (no double counting).

## Go / no-go

- **Go**: `restore_verify` / `verify-restore.sh` reports OK and the dashboard
  renders on the prior image.
- **No-go**: verification still fails -- do not resume traffic; escalate and
  restore an older backup.
