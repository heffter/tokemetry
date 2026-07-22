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
    # Newest dump. Filenames carry an ISO-ordered UTC stamp, so lexical order is
    # chronological; a glob avoids parsing `ls` output (SC2012) and works under
    # BusyBox find, which lacks `-printf`.
    for candidate in "$BACKUP_DIR"/tokemetry-*.sql.gz; do
        [ -f "$candidate" ] && dump="$candidate"
    done
fi
if [ -z "$dump" ] || [ ! -f "$dump" ]; then
    echo "[restore] no dump found (looked for: ${1:-latest in $BACKUP_DIR})" >&2
    exit 1
fi

# A restore must never run on an unverifiable dump. Require the checksum sidecar
# and a passing check; ALLOW_NO_CHECKSUM=1 is the explicit, loudly-warned escape
# hatch for legacy dumps taken before sidecars existed.
if [ -f "${dump}.sha256" ]; then
    echo "[restore] checking dump checksum"
    ( cd "$(dirname "$dump")" && sha256sum -c "$(basename "$dump").sha256" )
elif [ "${ALLOW_NO_CHECKSUM:-0}" = "1" ]; then
    echo "[restore] WARNING: no checksum sidecar for $dump; proceeding because ALLOW_NO_CHECKSUM=1" >&2
else
    echo "[restore] refusing: no checksum sidecar for $dump (set ALLOW_NO_CHECKSUM=1 to override)" >&2
    exit 1
fi

if [ "${FORCE:-0}" != "1" ]; then
    printf '[restore] This OVERWRITES database "%s". Type yes to continue: ' "$PGDATABASE"
    read -r answer
    [ "$answer" = "yes" ] || { echo "[restore] aborted"; exit 1; }
fi

# Stage decompression to a temp file so a truncated archive is caught before
# psql touches the live database, and run psql with ON_ERROR_STOP so a
# mid-restore SQL error aborts non-zero instead of psql exiting 0 on a partial
# restore.
sql_tmp="$(mktemp)"
trap 'rm -f "$sql_tmp"' EXIT

echo "[restore] decompressing $dump"
if ! gzip -dc "$dump" >"$sql_tmp"; then
    echo "[restore] decompression failed; aborting before touching $PGDATABASE" >&2
    exit 1
fi

echo "[restore] restoring $dump into $PGDATABASE"
psql --quiet --set ON_ERROR_STOP=1 --dbname "$PGDATABASE" -f "$sql_tmp" >/dev/null
echo "[restore] done -- run verify-restore.sh to confirm integrity"
