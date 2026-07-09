"""Dialect-aware idempotent upserts.

Both Postgres and SQLite (3.24+) support ``INSERT ... ON CONFLICT DO
UPDATE``; only the SQLAlchemy import differs. These helpers build the right
statement for the active dialect so ingest is exactly-once regardless of
backend.

The batches passed here must already be deduplicated on the conflict key:
Postgres rejects a statement that would update the same row twice, so the
ingest service collapses duplicates before calling in.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.dialects.postgresql import Insert as PgInsert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import Insert as SqliteInsert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

#: A dialect-specific INSERT exposing ``excluded`` and
#: ``on_conflict_do_update`` (both Postgres and SQLite support these).
DialectInsert = PgInsert | SqliteInsert

#: Columns replaced on a usage_events conflict (everything but the PK).
_EVENT_UPDATE_COLUMNS = (
    "machine",
    "session_id",
    "ts",
    "model",
    "project",
    "git_branch",
    "client_version",
    "entrypoint",
    "is_sidechain",
    "session_kind",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_short_tokens",
    "cache_write_long_tokens",
    "service_tier",
    "speed",
    "cost_usd",
    "provenance",
    "source",
    "extra",
)

#: Columns replaced on a daily_rollups conflict (everything but the PK/grain).
_ROLLUP_UPDATE_COLUMNS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_short_tokens",
    "cache_write_long_tokens",
    "total_tokens",
    "cost_usd",
    "provenance",
)


def _insert(dialect_name: str, table: Any) -> DialectInsert:
    """Return a dialect-specific INSERT construct for ``table``."""
    if dialect_name == "postgresql":
        return pg_insert(table)
    if dialect_name == "sqlite":
        return sqlite_insert(table)
    raise ValueError(f"unsupported dialect for upsert: {dialect_name}")


def usage_events_upsert(
    dialect_name: str, table: Any, rows: list[dict[str, Any]]
) -> DialectInsert:
    """Build a keep-max upsert for usage events.

    On conflict of ``(provider, event_id)`` the existing row is overwritten
    only when the incoming ``output_tokens`` is at least the stored value,
    resolving streaming-snapshot duplicates in favor of the settled record.
    """
    stmt = _insert(dialect_name, table).values(rows)
    excluded = stmt.excluded
    return stmt.on_conflict_do_update(
        index_elements=["provider", "event_id"],
        set_={name: excluded[name] for name in _EVENT_UPDATE_COLUMNS},
        where=excluded["output_tokens"] >= table.c.output_tokens,
    )


def daily_rollups_upsert(
    dialect_name: str, table: Any, rows: list[dict[str, Any]]
) -> DialectInsert:
    """Build an idempotent replace-upsert for daily rollups.

    Used for both bootstrap imports and event-derived refreshes: the grain
    ``(day, provider, machine, model, project)`` is overwritten (not
    accumulated), so recomputing a day converges to the same totals.
    """
    stmt = _insert(dialect_name, table).values(rows)
    excluded = stmt.excluded
    return stmt.on_conflict_do_update(
        index_elements=["day", "provider", "machine", "model", "project"],
        set_={name: excluded[name] for name in _ROLLUP_UPDATE_COLUMNS},
    )


def machine_upsert(dialect_name: str, table: Any, row: dict[str, Any]) -> DialectInsert:
    """Build an upsert that refreshes a machine's last_seen and metadata."""
    stmt = _insert(dialect_name, table).values([row])
    excluded = stmt.excluded
    return stmt.on_conflict_do_update(
        index_elements=["id"],
        set_={
            "platform": excluded["platform"],
            "last_seen": excluded["last_seen"],
            "collector_version": excluded["collector_version"],
        },
    )


#: Price columns replaced on a pricing conflict (everything but the grain).
_PRICING_UPDATE_COLUMNS = (
    "input_per_mtok",
    "output_per_mtok",
    "cache_read_per_mtok",
    "cache_write_short_per_mtok",
    "cache_write_long_per_mtok",
    "source",
)


def pricing_upsert(dialect_name: str, table: Any, rows: list[dict[str, Any]]) -> DialectInsert:
    """Build an upsert for price rows keyed on (provider, model, date)."""
    stmt = _insert(dialect_name, table).values(rows)
    excluded = stmt.excluded
    return stmt.on_conflict_do_update(
        index_elements=["provider", "model", "effective_date"],
        set_={name: excluded[name] for name in _PRICING_UPDATE_COLUMNS},
    )
