"""Shared query dependencies for the v2 read endpoints (Task 66.4).

A single ``query_filters`` dependency parses the uniform dimension and
pseudo-filters (FR-QUERY-002/011) so every v2 query endpoint accepts the same
filter surface, and ``normalize_range`` coerces the time bounds to UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Query

from tokemetry_server.services.query_framework import QueryFilters


def query_filters(
    provider: str | None = Query(default=None),
    native_model: str | None = Query(default=None, alias="model"),
    source: str | None = Query(default=None),
    machine: str | None = Query(default=None),
    project: str | None = Query(default=None),
    session_id: str | None = Query(default=None, alias="session"),
    environment: str | None = Query(default=None),
    outcome: str | None = Query(default=None),
    trace_id: str | None = Query(default=None),
    unknown_provider: bool = Query(default=False),
    unknown_model: bool = Query(default=False),
) -> QueryFilters:
    """Build a :class:`QueryFilters` from the uniform v2 filter query params."""
    return QueryFilters(
        provider=provider,
        native_model=native_model,
        source=source,
        machine=machine,
        project=project,
        session_id=session_id,
        environment=environment,
        outcome=outcome,
        trace_id=trace_id,
        unknown_provider=unknown_provider,
        unknown_model=unknown_model,
    )


def to_utc(value: datetime) -> datetime:
    """Coerce a query datetime to tz-aware UTC (naive inputs are assumed UTC)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
