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

# Newest dump. Filenames carry an ISO-ordered UTC stamp, so lexical order is
# chronological; a glob avoids parsing `ls` output (SC2012) and works under
# BusyBox find, which lacks `-printf`.
latest=""
for candidate in "$BACKUP_DIR"/tokemetry-*.sql.gz; do
    [ -f "$candidate" ] && latest="$candidate"
done
if [ -z "$latest" ]; then
    echo "[verify] no backup found in $BACKUP_DIR" >&2
    exit 1
fi
echo "[verify] latest backup: $latest"

# Refuse an unverifiable dump: proving the backup restores is the whole point,
# so a missing checksum is a hard failure (override with ALLOW_NO_CHECKSUM=1).
if [ -f "${latest}.sha256" ]; then
    echo "[verify] checking dump checksum"
    ( cd "$(dirname "$latest")" && sha256sum -c "$(basename "$latest").sha256" )
elif [ "${ALLOW_NO_CHECKSUM:-0}" = "1" ]; then
    echo "[verify] WARNING: no checksum sidecar for $latest; proceeding because ALLOW_NO_CHECKSUM=1" >&2
else
    echo "[verify] refusing: no checksum sidecar for $latest (set ALLOW_NO_CHECKSUM=1 to override)" >&2
    exit 1
fi

# Always drop the scratch database (and staged SQL) on exit -- including when
# the verifier below fails -- while preserving the triggering exit status so the
# job still fails loudly. Registered before createdb so an abort at any later
# step still cleans up.
sql_tmp=""
cleanup() {
    status=$?
    echo "[verify] dropping scratch database"
    dropdb --if-exists "$SCRATCH_DB" >/dev/null 2>&1 || true
    [ -n "$sql_tmp" ] && rm -f "$sql_tmp"
    exit "$status"
}
trap cleanup EXIT

echo "[verify] (re)creating scratch database $SCRATCH_DB"
dropdb --if-exists "$SCRATCH_DB"
createdb "$SCRATCH_DB"

sql_tmp="$(mktemp)"
echo "[verify] decompressing $latest"
if ! gzip -dc "$latest" >"$sql_tmp"; then
    echo "[verify] decompression failed" >&2
    exit 1
fi

echo "[verify] restoring into $SCRATCH_DB"
psql --quiet --set ON_ERROR_STOP=1 --dbname "$SCRATCH_DB" -f "$sql_tmp" >/dev/null

echo "[verify] verifying restored schema and integrity"
scratch_url="postgresql+psycopg://${PGUSER}@${PGHOST}/${SCRATCH_DB}"
python -m tokemetry_server.ops.restore_verify "$scratch_url"
# On success the EXIT trap drops the scratch database and exits 0.
