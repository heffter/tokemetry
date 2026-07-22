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
# not bash -- there is no `set -o pipefail`.

set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

mkdir -p "$BACKUP_DIR"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="$BACKUP_DIR/tokemetry-${stamp}.sql.gz"
tmp="${target}.tmp"
# Records pg_dump's exit status: in `pg_dump | gzip`, the pipeline status is
# gzip's, so a failed dump whose partial output still compresses cleanly would
# otherwise look successful. Writing pg_dump's status to a file recovers it
# without pipefail.
dump_status_file="${target}.pgstatus"

# Never leave a partial dump or scratch file behind on any early exit.
trap 'rm -f "$tmp" "$dump_status_file"' EXIT

echo "[backup] dumping ${PGDATABASE:-tokemetry} to ${target}"
set +e
( pg_dump --no-owner --no-privileges; echo "$?" >"$dump_status_file" ) | gzip -c >"$tmp"
gzip_status=$?
set -e

dump_status="$(cat "$dump_status_file" 2>/dev/null || echo 1)"
if [ "$dump_status" != "0" ]; then
    echo "[backup] pg_dump failed (status ${dump_status}); discarding partial dump" >&2
    exit 1
fi
if [ "$gzip_status" != "0" ]; then
    echo "[backup] gzip failed (status ${gzip_status}); discarding partial dump" >&2
    exit 1
fi
if [ ! -s "$tmp" ]; then
    echo "[backup] produced an empty dump; discarding" >&2
    exit 1
fi
# Prove the archive is a complete, decompressible gzip stream before publishing.
if ! gzip -t "$tmp"; then
    echo "[backup] archive failed gzip integrity check; discarding" >&2
    exit 1
fi

# Publish atomically: rename the validated temp into place, then write a
# checksum sidecar referencing the final name so a later truncation or tamper
# is caught before a restore (NFR-REL-004).
mv "$tmp" "$target"
echo "[backup] writing checksum ${target}.sha256"
sum="$(sha256sum "$target" | cut -d' ' -f1)"
echo "${sum}  $(basename "$target")" >"${target}.sha256"
# The archive is published; the trap only needs to clear the status file now.
trap 'rm -f "$dump_status_file"' EXIT

echo "[backup] pruning dumps older than ${RETENTION_DAYS} days"
find "$BACKUP_DIR" -name 'tokemetry-*.sql.gz' -type f -mtime "+${RETENTION_DAYS}" -delete
find "$BACKUP_DIR" -name 'tokemetry-*.sql.gz.sha256' -type f -mtime "+${RETENTION_DAYS}" -delete

echo "[backup] done"
