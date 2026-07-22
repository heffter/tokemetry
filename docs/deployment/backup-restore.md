# Backup and restore

Logical backups, an automated restore-verification job, and a manual restore
drill (Task 70.6, NFR-REL-004, AC-014).

## Backups

The `backup` service in `deploy/docker-compose.yml` runs `deploy/backup.sh`
nightly against Postgres:

- `pg_dump --no-owner --no-privileges`, streamed through `gzip` to
  `/backups/tokemetry-<UTC-stamp>.sql.gz`. A failed `pg_dump` aborts the run
  (its exit status is captured despite the pipe, which POSIX `sh` has no
  `pipefail` for), the archive is gzip-integrity-checked, and it is published
  atomically -- so a partial or empty dump is never left in place.
- A `.sha256` sidecar is written next to each completed dump so a later
  truncation or tamper is caught before a restore.
- Dumps (and their sidecars) older than `RETENTION_DAYS` (default 14) are
  pruned. This is file rotation, separate from database-row retention
  ([retention](../architecture/retention.md)).

For a **SQLite** deployment, back up with the online-safe snapshot:

```sh
sqlite3 /var/lib/tokemetry/tokemetry.sqlite3 ".backup '/backups/tokemetry-$(date -u +%Y%m%dT%H%M%SZ).sqlite3'"
```

The database path matches `TOKEMETRY_DATABASE_URL` in the native environment
file (`deploy/server/tokemetry-server.env.example`).

**Back up before every migration.** The upgrade procedure
([upgrades](server.md#upgrades)) is: take a fresh backup, then apply the new
image (which migrates on startup). Never migrate without a current backup -- a
failed migration is recovered by restoring the pre-migration dump.

## Restore verification (automated)

`deploy/verify-restore.sh` proves a backup is actually restorable and
trustworthy, without touching the live database:

1. Check the latest dump against its `.sha256` sidecar.
2. Restore it into a throwaway scratch database.
3. Run `python -m tokemetry_server.ops.restore_verify <scratch-url>`, which
   confirms the schema is at Alembic head, every expected table and view is
   present, and every `daily_rollups` row is internally consistent (its
   `total_tokens` equals the sum of its token tiers). A tampered or truncated
   backup fails one of these checks.
4. Drop the scratch database and exit non-zero on any failure.

Wire it as a CI job (against seeded fixtures, both engines) and as a cron unit
in production that alerts on a non-zero exit. The verifier is engine-agnostic
(SQLite and Postgres).

## Manual restore drill

Practice this quarterly so the real thing is routine.

1. Confirm a recent good backup exists and its checksum verifies:
   `sha256sum -c /backups/tokemetry-<stamp>.sql.gz.sha256`.
2. Stop the server (`docker compose stop server`).
3. Restore: `deploy/restore.sh /backups/tokemetry-<stamp>.sql.gz` (prompts
   before overwriting; `FORCE=1` to skip the prompt in automation).
4. Verify: `deploy/verify-restore.sh` (exit 0 = trustworthy).
5. Start the server and spot-check the dashboard.

## Recovery-time expectations

For the reference single-node deployment (one VPS, low-hundreds-of-MB
database):

- **RPO** (data loss window): up to 24 hours -- the interval between nightly
  backups. Shorten it by running `backup.sh` more often.
- **RTO** (time to restore): typically a few minutes -- decompress + `psql`
  restore + verify. Dominated by dump size and disk speed, not by row count.

Both scale with database size; measure them during the drill and record the
observed figures for the deployment.
