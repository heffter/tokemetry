#!/bin/sh
#
# Nightly Postgres backup for the tokemetry database.
#
# Purpose:   dump the database to a timestamped, compressed file and prune
#            dumps older than the retention window.
# Usage:     backup.sh   (connection comes from PG* environment variables)
# Prereqs:   pg_dump on PATH; PGHOST/PGUSER/PGPASSWORD/PGDATABASE set; a
#            writable /backups directory (a mounted volume).
#
# Runs under the postgres:16-alpine image (BusyBox sh), so this is POSIX sh,
# not bash.

set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

mkdir -p "$BACKUP_DIR"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="$BACKUP_DIR/tokemetry-${stamp}.sql.gz"

echo "[backup] dumping ${PGDATABASE:-tokemetry} to ${target}"
pg_dump --no-owner --no-privileges | gzip -c > "$target"

# Write a checksum sidecar so a truncated or tampered dump is detected before a
# restore is attempted (NFR-REL-004).
echo "[backup] writing checksum ${target}.sha256"
sha256sum "$target" > "${target}.sha256"

echo "[backup] pruning dumps older than ${RETENTION_DAYS} days"
find "$BACKUP_DIR" -name 'tokemetry-*.sql.gz' -type f -mtime "+${RETENTION_DAYS}" -delete
find "$BACKUP_DIR" -name 'tokemetry-*.sql.gz.sha256' -type f -mtime "+${RETENTION_DAYS}" -delete

echo "[backup] done"
