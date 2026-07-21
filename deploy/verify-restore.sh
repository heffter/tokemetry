#!/bin/sh
#
# Restore-verification for the tokemetry database (Task 70.6, NFR-REL-004).
#
# Purpose:   restore the latest backup into a throwaway scratch database, then
#            run the Python restore verifier (Alembic-head check, table
#            presence, daily_rollups integrity). Exits non-zero if the backup is
#            missing, its checksum fails, or verification fails -- so it can gate
#            a CI job or a cron alert.
# Usage:     verify-restore.sh
# Prereqs:   psql/createdb/dropdb on PATH; PG* env for the live server; SCRATCH_DB
#            names a scratch database on the same server; the tokemetry server
#            package importable as `python -m tokemetry_server.ops.restore_verify`.
#
# POSIX sh (runs under the postgres:16-alpine BusyBox sh).

set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
SCRATCH_DB="${SCRATCH_DB:-tokemetry_restore_check}"
PGHOST="${PGHOST:-localhost}"
PGUSER="${PGUSER:-tokemetry}"

latest="$(ls -1t "$BACKUP_DIR"/tokemetry-*.sql.gz 2>/dev/null | head -n 1 || true)"
if [ -z "$latest" ]; then
    echo "[verify] no backup found in $BACKUP_DIR" >&2
    exit 1
fi
echo "[verify] latest backup: $latest"

# Fail fast on a tampered or truncated dump.
if [ -f "${latest}.sha256" ]; then
    echo "[verify] checking dump checksum"
    ( cd "$(dirname "$latest")" && sha256sum -c "$(basename "$latest").sha256" )
else
    echo "[verify] WARNING: no checksum sidecar for $latest" >&2
fi

echo "[verify] (re)creating scratch database $SCRATCH_DB"
dropdb --if-exists "$SCRATCH_DB"
createdb "$SCRATCH_DB"

echo "[verify] restoring into $SCRATCH_DB"
gzip -dc "$latest" | psql --quiet --dbname "$SCRATCH_DB" >/dev/null

echo "[verify] verifying restored schema and integrity"
scratch_url="postgresql+psycopg://${PGUSER}@${PGHOST}/${SCRATCH_DB}"
python -m tokemetry_server.ops.restore_verify "$scratch_url"
status=$?

echo "[verify] dropping scratch database"
dropdb --if-exists "$SCRATCH_DB"

exit "$status"
