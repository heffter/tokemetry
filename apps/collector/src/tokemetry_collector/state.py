"""Local collector state: file offsets and a durable upload queue.

Backed by a single SQLite database so the collector is crash- and
offline-safe: parse progress (byte offsets per file) and pending uploads
survive restarts, and uploads only leave the queue once the server confirms
receipt. SQLite is accessed synchronously -- the collector is a simple
polling daemon, not a high-concurrency service.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS file_offsets (
    source TEXT NOT NULL,
    path   TEXT NOT NULL,
    offset INTEGER NOT NULL,
    size   INTEGER NOT NULL,
    PRIMARY KEY (source, path)
);
CREATE TABLE IF NOT EXISTS upload_queue (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    payload    TEXT NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class FileOffset:
    """Persisted parse progress for one source file."""

    offset: int
    size: int


@dataclass(frozen=True)
class QueuedBatch:
    """One pending upload: a batch destined for an ingest endpoint."""

    id: int
    kind: str
    payload: dict[str, Any]
    attempts: int


class CollectorState:
    """SQLite-backed offset store and upload queue."""

    def __init__(self, db_path: Path) -> None:
        """Open (creating if needed) the state database at ``db_path``."""
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        """Close the underlying database connection."""
        self._conn.close()

    def __enter__(self) -> CollectorState:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def get_offset(self, source: str, path: str) -> FileOffset | None:
        """Return stored parse progress for a file, or None if unseen."""
        row = self._conn.execute(
            "SELECT offset, size FROM file_offsets WHERE source = ? AND path = ?",
            (source, path),
        ).fetchone()
        if row is None:
            return None
        return FileOffset(offset=row["offset"], size=row["size"])

    def set_offset(self, source: str, path: str, offset: int, size: int) -> None:
        """Persist parse progress for a file."""
        self._conn.execute(
            """
            INSERT INTO file_offsets (source, path, offset, size)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (source, path) DO UPDATE SET offset = excluded.offset,
                                                     size = excluded.size
            """,
            (source, path, offset, size),
        )
        self._conn.commit()

    def enqueue(self, kind: str, payload: dict[str, Any]) -> int:
        """Append a batch to the upload queue; return its queue id."""
        cursor = self._conn.execute(
            "INSERT INTO upload_queue (kind, payload) VALUES (?, ?)",
            (kind, json.dumps(payload)),
        )
        self._conn.commit()
        return int(cursor.lastrowid or 0)

    def pending(self, limit: int) -> list[QueuedBatch]:
        """Return up to ``limit`` queued batches, oldest first."""
        rows = self._conn.execute(
            "SELECT id, kind, payload, attempts FROM upload_queue ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            QueuedBatch(
                id=row["id"],
                kind=row["kind"],
                payload=json.loads(row["payload"]),
                attempts=row["attempts"],
            )
            for row in rows
        ]

    def mark_uploaded(self, batch_id: int) -> None:
        """Remove a successfully uploaded batch from the queue."""
        self._conn.execute("DELETE FROM upload_queue WHERE id = ?", (batch_id,))
        self._conn.commit()

    def bump_attempts(self, batch_id: int) -> None:
        """Increment the retry counter for a batch that failed to upload."""
        self._conn.execute(
            "UPDATE upload_queue SET attempts = attempts + 1 WHERE id = ?",
            (batch_id,),
        )
        self._conn.commit()

    def queue_size(self) -> int:
        """Number of batches currently waiting to upload."""
        row = self._conn.execute("SELECT COUNT(*) AS n FROM upload_queue").fetchone()
        return int(row["n"])

    def get_meta(self, key: str) -> str | None:
        """Return a stored meta value, or None if unset."""
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def set_meta(self, key: str, value: str) -> None:
        """Store a meta key/value pair (upsert)."""
        self._conn.execute(
            """
            INSERT INTO meta (key, value) VALUES (?, ?)
            ON CONFLICT (key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        self._conn.commit()
