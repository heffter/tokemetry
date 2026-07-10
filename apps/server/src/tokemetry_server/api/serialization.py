"""Shared serialization helpers for API response schemas."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import PlainSerializer


def _ensure_utc_iso(value: datetime) -> str:
    """Serialize a datetime as offset-aware UTC ISO 8601.

    Datetimes read back from SQLite are timezone-naive because the database
    does not persist the offset. Emitting them without a ``+00:00`` suffix
    makes browsers parse them as *local* time, so every rendered timestamp is
    wrong by the viewer's UTC offset. Treat any naive value as UTC and always
    emit an explicit offset; convert aware values to UTC first.
    """
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.isoformat()


# A ``datetime`` that always serializes to offset-aware UTC ISO 8601 in JSON.
# Use this in every response schema field so the wire format is unambiguous.
UtcDatetime = Annotated[
    datetime,
    PlainSerializer(_ensure_utc_iso, return_type=str, when_used="json"),
]
