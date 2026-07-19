"""Shared query plumbing for the v2 read API (TOK-7, FR-QUERY-002..011).

Building blocks the v2 query endpoints share: keyset (cursor) pagination stable
under concurrent inserts (FR-QUERY-003), explicit per-resource sort parsing
(FR-QUERY-004), selectable aggregation grain (FR-QUERY-005), enforced bounded
time ranges for raw queries (NFR-PERF-004), uniform filter parsing with
unknown-provider/model pseudo-filters (FR-QUERY-002/011), and a data-quality
warning envelope collected for a queried range (FR-QUERY-010). These are pure,
provider-neutral helpers; endpoints wire them to their models and scopes.
"""

from __future__ import annotations

import base64
import binascii
import enum
import json
import operator
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.db import models


class QueryParamError(ValueError):
    """An invalid query parameter (bad sort, grain, cursor, or range)."""


# --- Sorting (FR-QUERY-004) -------------------------------------------------


@dataclass(frozen=True)
class SortSpec:
    """A resolved sort: a whitelisted field and a direction."""

    field: str
    descending: bool


def parse_sort(raw: str | None, allowed: frozenset[str], default: str) -> SortSpec:
    """Parse a ``sort`` parameter (``field`` or ``-field``) against a whitelist.

    Args:
        raw: The requested sort, or None to use ``default``.
        allowed: The fields this resource may be sorted by.
        default: The documented default sort (``field`` or ``-field``).

    Raises:
        QueryParamError: If the field is not in ``allowed``.
    """
    value = raw if raw else default
    descending = value.startswith("-")
    field = value[1:] if descending else value
    if field not in allowed:
        raise QueryParamError(
            f"sort field {field!r} is not one of {sorted(allowed)}"
        )
    return SortSpec(field=field, descending=descending)


# --- Aggregation grain (FR-QUERY-005) ---------------------------------------


class Grain(enum.Enum):
    """A time-aggregation grain for grouped queries."""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"


def parse_grain(raw: str | None, default: Grain = Grain.DAY) -> Grain:
    """Parse a ``grain`` parameter into a :class:`Grain`.

    Raises:
        QueryParamError: If the value is not a recognized grain.
    """
    if not raw:
        return default
    try:
        return Grain(raw)
    except ValueError:
        raise QueryParamError(
            f"grain {raw!r} is not one of {[g.value for g in Grain]}"
        ) from None


def truncate_to_grain(day: date, grain: Grain) -> date:
    """Truncate a date to the first day of its grain bucket (week starts Monday)."""
    if grain is Grain.DAY:
        return day
    if grain is Grain.WEEK:
        return day - timedelta(days=day.weekday())
    return day.replace(day=1)


# --- Bounded time ranges (NFR-PERF-004) -------------------------------------


def enforce_range_bound(start: datetime, end: datetime, max_days: int) -> None:
    """Reject a raw-query time range wider than ``max_days``.

    Raises:
        QueryParamError: If ``end`` precedes ``start`` or the span is too wide.
    """
    if end < start:
        raise QueryParamError("end must not precede start")
    if (end - start) > timedelta(days=max_days):
        raise QueryParamError(f"time range exceeds the {max_days}-day maximum")


# --- Keyset pagination (FR-QUERY-003) ---------------------------------------


@dataclass(frozen=True)
class Page:
    """One page of items plus the cursor to fetch the next page (None if last)."""

    items: list[Any]
    next_cursor: str | None


def encode_cursor(sort_value: Any, row_id: int | str) -> str:
    """Encode an opaque cursor from the last row's sort value and id tiebreaker.

    ``date``/``datetime`` sort values are stored as ISO strings so the cursor is
    JSON-portable; the endpoint coerces them back when comparing. The id
    tiebreaker may be an integer surrogate key or a string business key.
    """
    if isinstance(sort_value, datetime | date):
        encoded: Any = sort_value.isoformat()
    else:
        encoded = sort_value
    payload = json.dumps([encoded, row_id], separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str) -> tuple[Any, int | str]:
    """Decode a cursor into its ``(sort_value, row_id)``.

    Raises:
        QueryParamError: If the cursor is malformed.
    """
    try:
        payload = base64.urlsafe_b64decode(cursor.encode("ascii"))
        value, row_id = json.loads(payload)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise QueryParamError("invalid pagination cursor") from exc
    if not isinstance(row_id, int | str):
        raise QueryParamError("invalid pagination cursor")
    return value, row_id


def keyset_condition(
    sort_column: Any,
    id_column: Any,
    sort_value: Any,
    row_id: int | str,
    descending: bool,
) -> ColumnElement[bool]:
    """Build the ``WHERE`` for the row strictly after ``(sort_value, row_id)``.

    Uses the expanded row-value comparison (``sort > v OR (sort = v AND id >
    id)``) so it is portable across SQLite and Postgres and stable under
    concurrent inserts (the id tiebreaker keeps the total order strict).
    """
    after = operator.lt if descending else operator.gt
    return or_(
        after(sort_column, sort_value),
        and_(sort_column == sort_value, after(id_column, row_id)),
    )


def build_page(
    rows: list[Any], limit: int, cursor_of: Callable[[Any], str]
) -> Page:
    """Split ``limit + 1`` fetched rows into a page with a next cursor.

    The caller fetches ``limit + 1`` rows ordered by ``(sort, id)``; a full
    extra row means there is a next page, whose cursor is taken from the last
    returned item.
    """
    has_next = len(rows) > limit
    items = rows[:limit]
    next_cursor = cursor_of(items[-1]) if has_next and items else None
    return Page(items=items, next_cursor=next_cursor)


# --- Uniform filters (FR-QUERY-002/011) -------------------------------------


@dataclass(frozen=True)
class QueryFilters:
    """Common dimension filters plus the unknown-provider/model pseudo-filters."""

    provider: str | None = None
    native_model: str | None = None
    source: str | None = None
    machine: str | None = None
    project: str | None = None
    session_id: str | None = None
    agent: str | None = None
    environment: str | None = None
    outcome: str | None = None
    unknown_provider: bool = False
    unknown_model: bool = False


# --- Data-quality warning envelope (FR-QUERY-010) ---------------------------


@dataclass(frozen=True)
class QueryWarning:
    """A data-quality caveat attached to a query response."""

    kind: str
    detail: str
    count: int


async def collect_warnings(
    session: AsyncSession,
    start: datetime,
    end: datetime,
    stale_before: datetime | None = None,
) -> list[QueryWarning]:
    """Collect data-quality warnings for a queried range (FR-QUERY-010).

    Warns when the range contains unpriced/partial events, unknown-model
    observations, or (when ``stale_before`` is given) sources whose last
    successful ingest is older than that cutoff.
    """
    warnings: list[QueryWarning] = []

    unpriced = await _count_unpriced(session, start, end)
    if unpriced:
        warnings.append(
            QueryWarning("unpriced_events", "range contains unpriced or partial events", unpriced)
        )

    unknown = await _count_unknown_models(session, start, end)
    if unknown:
        warnings.append(
            QueryWarning("unknown_models", "range contains unknown-model observations", unknown)
        )

    if stale_before is not None:
        stale = await _count_stale_sources(session, stale_before)
        if stale:
            warnings.append(
                QueryWarning("stale_sources", "some sources have not ingested recently", stale)
            )
    return warnings


async def _count_unpriced(session: AsyncSession, start: datetime, end: datetime) -> int:
    cost = models.ComputedCost
    event = models.UsageEventV2
    stmt = (
        select(func.count())
        .select_from(cost)
        .join(event, (cost.provider == event.provider) & (cost.event_id == event.event_id))
        .where(
            cost.active.is_(True),
            cost.cost_status.in_(("unpriced", "partial")),
            event.ts_started >= start,
            event.ts_started <= end,
        )
    )
    return int(await session.scalar(stmt) or 0)


async def _count_unknown_models(
    session: AsyncSession, start: datetime, end: datetime
) -> int:
    dq = models.DataQualityEvent
    stmt = select(func.count()).where(
        dq.kind == "unknown_model", dq.ts >= start, dq.ts <= end
    )
    return int(await session.scalar(stmt) or 0)


async def _count_stale_sources(session: AsyncSession, stale_before: datetime) -> int:
    src = models.Source
    stmt = select(func.count()).where(
        src.revoked.is_(False),
        or_(
            src.last_successful_ingest.is_(None),
            src.last_successful_ingest < stale_before,
        ),
    )
    return int(await session.scalar(stmt) or 0)


def default_stale_before(now: datetime | None, stale_seconds: float) -> datetime:
    """The cutoff before which a source is considered stale for warnings."""
    reference = now if now is not None else datetime.now(UTC)
    return reference - timedelta(seconds=stale_seconds)
