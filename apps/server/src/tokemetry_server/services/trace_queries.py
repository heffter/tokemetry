"""Attempt, logical-request, and session read queries (TOK-7, FR-TRACE-006..012).

The trace surface of the v2 read API: keyset-paginated raw attempt listing
(FR-QUERY-003), logical requests joined with their attempt aggregates and an
ordered-attempt drilldown for the fallback-chain UI (FR-TRACE-006/007/012), and
session rollups keyed by the scoped identity ``(provider, source, session_id)``
(FR-TRACE-010/011). Token and cost totals are computed from attempt events only,
never from logical-request summaries.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.db import models
from tokemetry_server.services.query_framework import (
    Page,
    QueryFilters,
    QueryParamError,
    build_page,
    decode_cursor,
    encode_cursor,
    keyset_condition,
)

_ATTEMPT = (models.UsageEventV2.event_kind == "attempt", models.UsageEventV2.finality == "final")


@dataclass(frozen=True)
class AttemptRow:
    """One final attempt event with its lifecycle and usage fields."""

    event_id: str
    provider: str
    native_model: str
    requested_model: str | None
    routed_model: str | None
    ts_started: datetime
    ts_completed: datetime | None
    latency_ms: int | None
    success: bool
    logical_request_id: str | None
    session_id: str | None
    source: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_short_tokens: int
    cache_write_long_tokens: int
    reasoning_tokens: int
    cost_usd: Decimal | None
    trace_id: str | None
    span_id: str | None
    parent_span_id: str | None
    agent_id: str | None


@dataclass(frozen=True)
class RequestRow:
    """A logical request with its attempt-chain aggregates."""

    provider: str
    logical_request_id: str
    requested_model: str | None
    session_id: str | None
    routing_policy: str | None
    routing_reason: str | None
    attempt_count: int
    fallback_count: int
    winning_attempt_id: str | None
    ts_first: datetime | None
    ts_last: datetime | None
    total_tokens: int
    cost_usd: Decimal | None


@dataclass(frozen=True)
class RequestDetail:
    """A logical request plus its ordered attempts (winning one marked)."""

    request: RequestRow
    attempts: list[AttemptRow]


@dataclass(frozen=True)
class SessionRow:
    """A session rollup keyed by the scoped identity (provider, source, session)."""

    scoped_id: str
    provider: str
    source: str
    session_id: str
    attempt_count: int
    total_tokens: int
    cost_usd: Decimal | None
    ts_first: datetime | None
    ts_last: datetime | None


def scoped_session_id(provider: str, source: str, session_id: str) -> str:
    """Encode a session's scoped identity into one opaque URL-safe token."""
    payload = json.dumps([provider, source, session_id], separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def decode_scoped_session_id(scoped_id: str) -> tuple[str, str, str]:
    """Decode a scoped session id; raise :class:`QueryParamError` if malformed."""
    try:
        payload = base64.urlsafe_b64decode(scoped_id.encode("ascii"))
        provider, source, session_id = json.loads(payload)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise QueryParamError("invalid scoped session id") from exc
    return str(provider), str(source), str(session_id)


def _apply_attempt_filters(statement: Any, filters: QueryFilters) -> Any:
    """Apply dimension filters common to attempt-backed queries."""
    event = models.UsageEventV2
    pairs = {
        event.provider: filters.provider,
        event.native_model: filters.native_model,
        event.machine: filters.machine,
        event.session_id: filters.session_id,
        event.trace_id: filters.trace_id,
    }
    for column, value in pairs.items():
        if value is not None:
            statement = statement.where(column == value)
    if filters.outcome is not None:
        statement = statement.where(event.success.is_(filters.outcome == "success"))
    return statement


async def list_attempts(
    session: AsyncSession,
    start: datetime,
    end: datetime,
    filters: QueryFilters,
    logical_request_id: str | None,
    cursor: str | None,
    limit: int,
) -> Page:
    """Keyset-paginated newest-first listing of final attempt events."""
    event = models.UsageEventV2
    src = models.Source
    statement = (
        select(event, func.coalesce(src.name, "").label("source"))
        .join(src, src.id == event.source_id, isouter=True)
        .where(*_ATTEMPT, event.ts_started >= start, event.ts_started <= end)
    )
    statement = _apply_attempt_filters(statement, filters)
    if logical_request_id is not None:
        statement = statement.where(event.logical_request_id == logical_request_id)
    if cursor is not None:
        value, row_id = decode_cursor(cursor)
        statement = statement.where(
            keyset_condition(
                event.ts_started, event.event_id,
                datetime.fromisoformat(str(value)), row_id, descending=True,
            )
        )
    statement = statement.order_by(event.ts_started.desc(), event.event_id.desc()).limit(
        limit + 1
    )
    rows = [_attempt_row(row[0], row[1]) for row in (await session.execute(statement)).all()]
    return build_page(rows, limit, lambda r: encode_cursor(r.ts_started, r.event_id))


def _attempt_row(event: models.UsageEventV2, source: str) -> AttemptRow:
    return AttemptRow(
        event_id=event.event_id, provider=event.provider, native_model=event.native_model,
        requested_model=event.requested_model, routed_model=event.routed_model,
        ts_started=event.ts_started, ts_completed=event.ts_completed,
        latency_ms=event.latency_ms, success=event.success,
        logical_request_id=event.logical_request_id, session_id=event.session_id,
        source=source, input_tokens=event.input_tokens, output_tokens=event.output_tokens,
        cache_read_tokens=event.cache_read_tokens,
        cache_write_short_tokens=event.cache_write_short_tokens,
        cache_write_long_tokens=event.cache_write_long_tokens,
        reasoning_tokens=event.reasoning_tokens, cost_usd=event.cost_usd,
        trace_id=event.trace_id, span_id=event.span_id,
        parent_span_id=event.parent_span_id, agent_id=event.agent_id,
    )


async def _attempt_totals(
    session: AsyncSession, provider: str, logical_request_id: str
) -> tuple[int, Decimal | None]:
    """Sum tokens and transitional cost over a logical request's attempts."""
    event = models.UsageEventV2
    total = (
        event.input_tokens + event.output_tokens + event.cache_read_tokens
        + event.cache_write_short_tokens + event.cache_write_long_tokens
        + event.reasoning_tokens
    )
    row = (
        await session.execute(
            select(func.coalesce(func.sum(total), 0), func.sum(event.cost_usd)).where(
                *_ATTEMPT, event.provider == provider,
                event.logical_request_id == logical_request_id,
            )
        )
    ).one()
    return int(row[0] or 0), None if row[1] is None else Decimal(str(row[1]))


async def list_requests(
    session: AsyncSession,
    start: datetime,
    end: datetime,
    routing_policy: str | None,
    fallback_only: bool,
    cursor: str | None,
    limit: int,
) -> Page:
    """Keyset-paginated logical requests with attempt-chain aggregates."""
    lr = models.LogicalRequest
    statement = select(lr).where(lr.ts_last >= start, lr.ts_last <= end)
    if routing_policy is not None:
        statement = statement.where(lr.routing_policy == routing_policy)
    if fallback_only:
        statement = statement.where(lr.fallback_count > 0)
    if cursor is not None:
        value, row_id = decode_cursor(cursor)
        statement = statement.where(
            keyset_condition(
                lr.ts_last, lr.logical_request_id,
                datetime.fromisoformat(str(value)), row_id, descending=True,
            )
        )
    statement = statement.order_by(lr.ts_last.desc(), lr.logical_request_id.desc()).limit(
        limit + 1
    )
    requests = (await session.execute(statement)).scalars().all()
    rows = [await _request_row(session, request) for request in requests]
    return build_page(
        rows, limit, lambda r: encode_cursor(r.ts_last, r.logical_request_id)
    )


async def _request_row(session: AsyncSession, request: models.LogicalRequest) -> RequestRow:
    tokens, cost = await _attempt_totals(session, request.provider, request.logical_request_id)
    return RequestRow(
        provider=request.provider, logical_request_id=request.logical_request_id,
        requested_model=request.requested_model, session_id=request.session_id,
        routing_policy=request.routing_policy, routing_reason=request.routing_reason,
        attempt_count=request.attempt_count, fallback_count=request.fallback_count,
        winning_attempt_id=request.winning_attempt_id, ts_first=request.ts_first,
        ts_last=request.ts_last, total_tokens=tokens, cost_usd=cost,
    )


async def request_detail(
    session: AsyncSession, provider: str, logical_request_id: str
) -> RequestDetail | None:
    """A logical request with its attempts ordered by sequence for the drilldown."""
    request = await session.get(models.LogicalRequest, (provider, logical_request_id))
    if request is None:
        return None
    event = models.UsageEventV2
    src = models.Source
    statement = (
        select(event, func.coalesce(src.name, "").label("source"))
        .join(src, src.id == event.source_id, isouter=True)
        .where(
            event.provider == provider,
            event.logical_request_id == logical_request_id,
            event.event_kind == "attempt",
            event.finality == "final",
        )
        .order_by(event.sequence.asc(), event.ts_started.asc())
    )
    attempts = [_attempt_row(r[0], r[1]) for r in (await session.execute(statement)).all()]
    return RequestDetail(request=await _request_row(session, request), attempts=attempts)


async def list_sessions(
    session: AsyncSession,
    start: datetime,
    end: datetime,
    filters: QueryFilters,
    cursor: str | None,
    limit: int,
) -> Page:
    """Keyset-paginated session rollups by scoped identity (provider, source, session)."""
    event = models.UsageEventV2
    src = models.Source
    source_name = func.coalesce(src.name, "").label("source")
    session_key = func.coalesce(event.session_id, "").label("session")
    total = (
        event.input_tokens + event.output_tokens + event.cache_read_tokens
        + event.cache_write_short_tokens + event.cache_write_long_tokens
        + event.reasoning_tokens
    )
    statement = (
        select(
            event.provider, source_name, session_key,
            func.count(), func.coalesce(func.sum(total), 0), func.sum(event.cost_usd),
            func.min(event.ts_started), func.max(event.ts_started),
        )
        .join(src, src.id == event.source_id, isouter=True)
        .where(*_ATTEMPT, event.ts_started >= start, event.ts_started <= end)
    )
    statement = _apply_attempt_filters(statement, filters)
    statement = statement.group_by(event.provider, source_name, session_key)
    # Grouped sessions have no stable keyset, so paginate by offset (the range is
    # bounded); the cursor carries the next offset.
    offset = _cursor_offset(cursor)
    statement = (
        statement.order_by(event.provider, source_name, session_key)
        .offset(offset)
        .limit(limit + 1)
    )
    rows = [
        SessionRow(
            scoped_id=scoped_session_id(str(r[0]), str(r[1]), str(r[2])),
            provider=str(r[0]), source=str(r[1]), session_id=str(r[2]),
            attempt_count=int(r[3] or 0), total_tokens=int(r[4] or 0),
            cost_usd=None if r[5] is None else Decimal(str(r[5])),
            ts_first=r[6], ts_last=r[7],
        )
        for r in (await session.execute(statement)).all()
    ]
    return build_page(rows, limit, lambda r: encode_cursor("", offset + limit))


def _cursor_offset(cursor: str | None) -> int:
    """Decode an offset cursor (0 when absent or malformed value)."""
    if cursor is None:
        return 0
    _, offset = decode_cursor(cursor)
    return offset if isinstance(offset, int) else 0


async def session_detail(
    session: AsyncSession, provider: str, source: str, session_id: str
) -> SessionRow | None:
    """One session rollup by its scoped identity, or None if it has no attempts."""
    event = models.UsageEventV2
    src = models.Source
    total = (
        event.input_tokens + event.output_tokens + event.cache_read_tokens
        + event.cache_write_short_tokens + event.cache_write_long_tokens
        + event.reasoning_tokens
    )
    row = (
        await session.execute(
            select(
                func.count(), func.coalesce(func.sum(total), 0), func.sum(event.cost_usd),
                func.min(event.ts_started), func.max(event.ts_started),
            )
            .join(src, src.id == event.source_id, isouter=True)
            .where(
                *_ATTEMPT, event.provider == provider,
                func.coalesce(event.session_id, "") == session_id,
                func.coalesce(src.name, "") == source,
            )
        )
    ).one()
    if not row[0]:
        return None
    return SessionRow(
        scoped_id=scoped_session_id(provider, source, session_id),
        provider=provider, source=source, session_id=session_id,
        attempt_count=int(row[0]), total_tokens=int(row[1] or 0),
        cost_usd=None if row[2] is None else Decimal(str(row[2])),
        ts_first=row[3], ts_last=row[4],
    )
