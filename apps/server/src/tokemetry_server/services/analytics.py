"""Time-based analytics: current limits, burn rate, predictions, blocks.

These read from ``limit_snapshots`` (official utilization over time) and
``usage_events`` (token flow). Predictions extrapolate the recent slope of
official utilization rather than guessing an absolute token budget, so they
stay accurate for subscription plans whose limits are not published.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.db import models

#: Length of an Anthropic subscription usage block.
BLOCK_LENGTH = timedelta(hours=5)

#: Window kind whose resets anchor the 5-hour block grid.
_FIVE_HOUR = "five_hour"


def _as_utc(value: datetime) -> datetime:
    """Ensure a DB datetime is timezone-aware (UTC)."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _now() -> datetime:
    """Current UTC time (indirected for tests)."""
    return datetime.now(UTC)


async def current_limits(session: AsyncSession) -> list[models.LimitSnapshot]:
    """Return the most recent snapshot for each (provider, window_kind)."""
    limit = models.LimitSnapshot
    latest = (
        select(
            limit.provider,
            limit.window_kind,
            func.max(limit.ts).label("mts"),
        )
        .group_by(limit.provider, limit.window_kind)
        .subquery()
    )
    statement = select(limit).join(
        latest,
        and_(
            limit.provider == latest.c.provider,
            limit.window_kind == latest.c.window_kind,
            limit.ts == latest.c.mts,
        ),
    )
    result = await session.execute(statement)
    return list(result.scalars())


async def limits_history(
    session: AsyncSession,
    window_kind: str,
    start: datetime,
    end: datetime,
) -> list[models.LimitSnapshot]:
    """Return snapshots for one window kind in a time range, oldest first."""
    limit = models.LimitSnapshot
    statement = (
        select(limit)
        .where(limit.window_kind == window_kind, limit.ts >= start, limit.ts <= end)
        .order_by(limit.ts)
    )
    result = await session.execute(statement)
    return list(result.scalars())


async def token_burn_rate(
    session: AsyncSession, now: datetime | None = None, window_minutes: int = 60
) -> float:
    """Return tokens per minute over the trailing window."""
    reference = now if now is not None else _now()
    since = reference - timedelta(minutes=window_minutes)
    event = models.UsageEvent
    total = (
        event.input_tokens
        + event.output_tokens
        + event.cache_read_tokens
        + event.cache_write_short_tokens
        + event.cache_write_long_tokens
    )
    statement = select(func.coalesce(func.sum(total), 0)).where(event.ts >= since)
    result = await session.execute(statement)
    summed = int(result.scalar_one() or 0)
    return summed / window_minutes


@dataclass(frozen=True)
class Prediction:
    """Extrapolated exhaustion time for a limit window."""

    window_kind: str
    utilization_pct: float
    slope_pct_per_min: float
    predicted_exhaustion_at: datetime | None
    resets_at: datetime | None


async def predict_exhaustion(
    session: AsyncSession,
    window_kind: str = _FIVE_HOUR,
    now: datetime | None = None,
    lookback_minutes: int = 90,
) -> Prediction | None:
    """Predict when a window reaches 100% from its recent utilization slope.

    Returns None when there are too few snapshots to estimate a slope.
    """
    reference = now if now is not None else _now()
    since = reference - timedelta(minutes=lookback_minutes)
    snapshots = await limits_history(session, window_kind, since, reference)
    if len(snapshots) < 2:
        return None

    first, last = snapshots[0], snapshots[-1]
    minutes = (_as_utc(last.ts) - _as_utc(first.ts)).total_seconds() / 60.0
    if minutes <= 0:
        return None
    slope = (float(last.utilization_pct) - float(first.utilization_pct)) / minutes

    predicted: datetime | None = None
    if slope > 0 and last.utilization_pct < 100:
        minutes_left = (100.0 - float(last.utilization_pct)) / slope
        predicted = _as_utc(last.ts) + timedelta(minutes=minutes_left)

    return Prediction(
        window_kind=window_kind,
        utilization_pct=float(last.utilization_pct),
        slope_pct_per_min=slope,
        predicted_exhaustion_at=predicted,
        resets_at=_as_utc(last.resets_at) if last.resets_at else None,
    )


@dataclass(frozen=True)
class Block:
    """One reconstructed 5-hour usage block."""

    start: datetime
    end: datetime
    total_tokens: int
    cost_usd: Decimal | None
    peak_tokens_per_min: int
    end_utilization_pct: float | None


async def _block_anchor(session: AsyncSession, fallback: datetime) -> datetime:
    """Return the block-grid anchor: a known five_hour reset, else fallback."""
    limit = models.LimitSnapshot
    statement = (
        select(limit.resets_at)
        .where(limit.window_kind == _FIVE_HOUR, limit.resets_at.is_not(None))
        .order_by(limit.ts.desc())
        .limit(1)
    )
    result = await session.execute(statement)
    reset = result.scalar_one_or_none()
    return _as_utc(reset) if reset is not None else fallback


async def blocks(
    session: AsyncSession, start: datetime, end: datetime
) -> list[Block]:
    """Reconstruct 5-hour usage blocks over a time range.

    Blocks are aligned to the most recent official five_hour reset time when
    one exists (so boundaries match Anthropic's), otherwise to the first
    event in range. Each block reports token/cost totals, the peak
    per-minute token burn, and the utilization at the block's end.
    """
    event = models.UsageEvent
    total = (
        event.input_tokens
        + event.output_tokens
        + event.cache_read_tokens
        + event.cache_write_short_tokens
        + event.cache_write_long_tokens
    )
    rows = (
        await session.execute(
            select(event.ts, total, event.cost_usd)
            .where(event.ts >= start, event.ts <= end)
            .order_by(event.ts)
        )
    ).all()
    if not rows:
        return []

    anchor = await _block_anchor(session, _as_utc(rows[0][0]))
    buckets: dict[int, list[tuple[datetime, int, Decimal | None]]] = {}
    for ts, tokens, cost in rows:
        aware = _as_utc(ts)
        index = int((aware - anchor) // BLOCK_LENGTH)
        buckets.setdefault(index, []).append((aware, int(tokens or 0), cost))

    five_hour = await current_limits(session)
    end_utilization = next(
        (float(s.utilization_pct) for s in five_hour if s.window_kind == _FIVE_HOUR),
        None,
    )

    result: list[Block] = []
    for index in sorted(buckets):
        block_start = anchor + index * BLOCK_LENGTH
        block_end = block_start + BLOCK_LENGTH
        entries = buckets[index]
        total_tokens = sum(tokens for _, tokens, _ in entries)
        costs = [cost for _, _, cost in entries if cost is not None]
        cost_sum = sum(costs, Decimal("0")) if costs else None
        result.append(
            Block(
                start=block_start,
                end=block_end,
                total_tokens=total_tokens,
                cost_usd=cost_sum,
                peak_tokens_per_min=_peak_per_minute(entries),
                end_utilization_pct=(end_utilization if index == max(buckets) else None),
            )
        )
    return result


def _peak_per_minute(entries: list[tuple[datetime, int, Decimal | None]]) -> int:
    """Return the largest single-minute token sum within a block."""
    per_minute: dict[datetime, int] = {}
    for ts, tokens, _ in entries:
        minute = ts.replace(second=0, microsecond=0)
        per_minute[minute] = per_minute.get(minute, 0) + tokens
    return max(per_minute.values(), default=0)
