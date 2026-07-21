# Migration runbook

Applying an Alembic schema migration to a running tokemetry server
(AC-013). The server migrates to head on startup, so in the normal single-node
flow a migration is applied by deploying a newer image; this runbook is the
controlled procedure and its go/no-go criteria.

## Pre-flight

1. **Back up first (mandatory).** Take a fresh dump and confirm its checksum
   ([backup-restore](../deployment/backup-restore.md)). Never migrate without a
   current, verified backup -- it is the only rollback for an irreversible
   migration.
2. Note the current schema head:
   `docker compose exec server python -c "from tokemetry_server.db.migrate import alembic_config; from alembic.script import ScriptDirectory; print(ScriptDirectory.from_config(alembic_config('sqlite://')).get_current_head())"`
   and the database's current revision (the `alembic_version` table).
3. Read the new migrations' `upgrade()`/`downgrade()` to confirm each is
   reversible, and whether any is destructive (drops a column/table).

## Maintenance window

1. Announce a short read-only window if the migration rewrites large tables.
2. `docker compose stop server` (leave the database running).

## Apply

1. Pull the new image and start the server; startup runs
   `upgrade_to_head`. Alternatively run Alembic directly:
   `docker compose run --rm server alembic upgrade head`.
2. Watch the logs for the `Running upgrade <from> -> <to>` lines to complete
   with no error.

## Verification

Run the restore/integrity checks against the live database:

- `python -m tokemetry_server.ops.restore_verify <sync-url>` -- confirms the
  schema is at head, all tables/views exist, and `daily_rollups` are
  internally consistent.
- The migration/ORM drift test (`test_migration_matches_orm_metadata`) and the
  view/backfill verifier (Task 62.8) run green in CI for the release.
- Spot-check the dashboard: usage, costs, and limits render.

## Go / no-go

- **Go**: all `Running upgrade` lines completed; `restore_verify` reports OK
  (`at_head=true`, no missing tables, zero rollup inconsistencies); the
  dashboard renders.
- **No-go**: any migration step errored, the verifier reports not-at-head or an
  inconsistency, or reads fail. Follow the [rollback runbook](rollback.md)
  immediately: downgrade if the migrations are reversible, otherwise restore
  the pre-flight backup.

## Failure modes

- *Migration errors mid-run*: Alembic runs each migration in a transaction on
  Postgres, so a failed migration rolls itself back; re-check the revision and
  fix forward or roll back. SQLite DDL is not transactional -- restore from
  backup if a SQLite migration fails partway.
- *Startup migrate loop*: if the server crash-loops on a bad migration, set
  `TOKEMETRY_AUTO_MIGRATE=false`, start it, and apply/repair Alembic manually.
