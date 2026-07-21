#!/bin/sh
#
# Restore the tokemetry database from a backup (Task 70.6).
#
# Purpose:   restore a chosen (or the latest) dump into the live database, after
#            verifying its checksum. Destructive: the target database is
#            overwritten, so it prompts unless FORCE=1.
# Usage:     restore.sh [path/to/tokemetry-<stamp>.sql.gz]
#            (no argument restores the most recent dump in BACKUP_DIR)
# Prereqs:   psql on PATH; PG* env pointing at the target server/database.
#
# POSIX sh (runs under the postgres:16-alpine BusyBox sh).

set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
PGDATABASE="${PGDATABASE:-tokemetry}"

dump="${1:-}"
if [ -z "$dump" ]; then
    dump="$(ls -1t "$BACKUP_DIR"/tokemetry-*.sql.gz 2>/dev/null | head -n 1 || true)"
fi
if [ -z "$dump" ] || [ ! -f "$dump" ]; then
    echo "[restore] no dump found (looked for: ${1:-latest in $BACKUP_DIR})" >&2
    exit 1
fi

if [ -f "${dump}.sha256" ]; then
    echo "[restore] checking dump checksum"
    ( cd "$(dirname "$dump")" && sha256sum -c "$(basename "$dump").sha256" )
else
    echo "[restore] WARNING: no checksum sidecar for $dump" >&2
fi

if [ "${FORCE:-0}" != "1" ]; then
    printf '[restore] This OVERWRITES database "%s". Type yes to continue: ' "$PGDATABASE"
    read -r answer
    [ "$answer" = "yes" ] || { echo "[restore] aborted"; exit 1; }
fi

echo "[restore] restoring $dump into $PGDATABASE"
gzip -dc "$dump" | psql --quiet --dbname "$PGDATABASE" >/dev/null
echo "[restore] done -- run verify-restore.sh to confirm integrity"
