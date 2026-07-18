"""Logical-request grouping: attempts, fallbacks, and the winning attempt.

A logical request (D-003, FR-TRACE-001/002) groups the attempts of one upstream
request -- retries and provider fallbacks -- so the dashboard can show the whole
cascade without double-counting usage. This service maintains the
``logical_requests`` row for a ``(provider, logical_request_id)`` by
**recomputing** it from the current ``usage_events_v2`` rows rather than
incrementing counters, so it is correct regardless of arrival order (a fallback
landing before the original), streamed snapshots superseded by finals, replays,
or an admin correction that changes an attempt (FR-EVENT-026).

Only ``attempt`` events contribute to ``attempt_count``, ``fallback_count``, the
timestamps, and the winning attempt; a ``logical_request`` summary event updates
metadata only and never adds billable usage (FR-EVENT-004, FR-TRACE-007) -- and
because the v1 compatibility view and rollups already filter to attempts, summary
rows never affect token or cost sums (FR-TRACE-003/005).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.db import models


def _attempt_id(row: models.UsageEventV2) -> str:
    """The attempt's identifier: its ``attempt_id`` or, absent that, event id."""
    return row.attempt_id or row.event_id


def _completed_at(row: models.UsageEventV2) -> datetime:
    """The attempt's completion time, falling back to its start time."""
    return row.ts_completed or row.ts_started


class LogicalRequestService:
    """Recomputes ``logical_requests`` rows from the v2 ledger."""

    def __init__(self, session: AsyncSession) -> None:
        """Create the service bound to the caller's transaction."""
        self._session = session

    async def recompute(self, provider: str, logical_request_id: str) -> None:
        """Rebuild the ``logical_requests`` row from its current ledger rows."""
        rows = (
            (
                await self._session.execute(
                    select(models.UsageEventV2).where(
                        models.UsageEventV2.provider == provider,
                        models.UsageEventV2.logical_request_id == logical_request_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        attempts = [row for row in rows if row.event_kind == "attempt"]
        summary = next(
            (row for row in rows if row.event_kind == "logical_request"), None
        )
        if not attempts and summary is None:
            return

        aggregates = _aggregate(attempts, summary)
        record = await self._session.get(
            models.LogicalRequest, (provider, logical_request_id)
        )
        if record is None:
            record = models.LogicalRequest(
                provider=provider, logical_request_id=logical_request_id
            )
            self._session.add(record)
        for name, value in aggregates.items():
            setattr(record, name, value)


def _aggregate(
    attempts: list[models.UsageEventV2], summary: models.UsageEventV2 | None
) -> dict[str, Any]:
    """Derive the logical-request aggregates from its attempts and summary."""
    fallback_count = sum(
        1 for attempt in attempts if (attempt.routing or {}).get("fallback_from")
    )
    ts_first = min((a.ts_started for a in attempts), default=None)
    ts_last = max((_completed_at(a) for a in attempts), default=None)

    winners = [a for a in attempts if a.success and a.finality == "final"]
    # On multiple successes, the last completed attempt wins (FR-TRACE-004).
    winning = max(winners, key=_completed_at) if winners else None

    # Metadata comes from the summary event when present, else the first attempt.
    first_attempt = min(attempts, key=lambda a: a.ts_started) if attempts else None
    meta_source = summary or first_attempt
    routing = (meta_source.routing or {}) if meta_source is not None else {}

    return {
        "requested_model": meta_source.requested_model if meta_source else None,
        "session_id": meta_source.session_id if meta_source else None,
        "routing_policy": routing.get("policy"),
        "routing_reason": routing.get("reason"),
        "attempt_count": len(attempts),
        "fallback_count": fallback_count,
        "winning_attempt_id": _attempt_id(winning) if winning else None,
        "ts_first": ts_first,
        "ts_last": ts_last,
    }
