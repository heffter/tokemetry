"""Provider-neutral live-overview aggregation for NowView (Task 73, FR-UI-001).

Composes the dashboard front-page snapshot from the v2 query framework so it
honors the uniform dimension filters: a filtered token burn rate, per-provider
live limits (with a burn-based exhaustion estimate bounded by the window reset),
and today's usage composition by native model. Read-only; the caller owns the
session.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.db import models
from tokemetry_server.services import analytics
from tokemetry_server.services.queries_v2 import grouped_usage
from tokemetry_server.services.query_framework import QueryFilters

#: Trailing window for the live burn-rate estimate.
BURN_WINDOW_MINUTES = 15

#: Limit units the token burn rate can deplete (others get no burn estimate).
_TOKEN_UNITS = frozenset({None, "tokens", "token"})


@dataclass(frozen=True)
class ProviderLimitLive:
    """One provider limit window's current state and exhaustion estimate."""

    provider: str
    window_kind: str
    utilization_pct: float
    limit_amount: float | None
    remaining: float | None
    unit: str | None
    resets_at: datetime | None
    predicted_exhaustion_at: datetime | None


@dataclass(frozen=True)
class ModelUsageLive:
    """Today's token total for one native model."""

    native_model: str
    total_tokens: int


@dataclass(frozen=True)
class LiveOverview:
    """The provider-neutral live overview for the dashboard front page."""

    now: datetime
    burn_rate_per_min: float
    provider_limits: list[ProviderLimitLive]
    today_by_model: list[ModelUsageLive]


async def _filtered_burn_rate(
    session: AsyncSession,
    filters: QueryFilters,
    now: datetime,
    window_minutes: int,
) -> float:
    """Tokens/min over the trailing window, honoring the dimension filters."""
    since = now - timedelta(minutes=window_minutes)
    rows = await grouped_usage(session, "provider", since, now, filters)
    total = sum(row.total_tokens for row in rows)
    return total / window_minutes


def _limit_live(
    snapshot: models.LimitSnapshot, burn_per_min: float, now: datetime
) -> ProviderLimitLive:
    resets_at, _ = analytics.roll_reset_forward(
        snapshot.window_kind, snapshot.resets_at, now
    )
    remaining = snapshot.remaining
    unit = snapshot.unit
    predicted: datetime | None = None
    if remaining is not None and burn_per_min > 0 and unit in _TOKEN_UNITS:
        minutes_left = float(remaining) / burn_per_min
        candidate = now + timedelta(minutes=minutes_left)
        # A reset that comes first means the window refills before exhaustion.
        if resets_at is None or candidate <= resets_at:
            predicted = candidate
    return ProviderLimitLive(
        provider=snapshot.provider,
        window_kind=snapshot.window_kind,
        utilization_pct=float(snapshot.utilization_pct),
        limit_amount=snapshot.limit_amount,
        remaining=remaining,
        unit=unit,
        resets_at=resets_at,
        predicted_exhaustion_at=predicted,
    )


async def build_live_overview(
    session: AsyncSession,
    filters: QueryFilters,
    now: datetime,
    *,
    burn_window_minutes: int = BURN_WINDOW_MINUTES,
) -> LiveOverview:
    """Build the filtered live overview at ``now``."""
    burn = await _filtered_burn_rate(session, filters, now, burn_window_minutes)

    limits = await analytics.current_limits(session)
    if filters.provider is not None:
        limits = [lim for lim in limits if lim.provider == filters.provider]
    provider_limits = sorted(
        (_limit_live(lim, burn, now) for lim in limits),
        key=lambda item: (item.provider, item.window_kind),
    )

    today = now.date()
    today_start = datetime(today.year, today.month, today.day, tzinfo=UTC)
    model_rows = await grouped_usage(session, "model", today_start, now, filters)
    today_by_model = sorted(
        (ModelUsageLive(row.key, row.total_tokens) for row in model_rows),
        key=lambda item: item.total_tokens,
        reverse=True,
    )

    return LiveOverview(
        now=now,
        burn_rate_per_min=burn,
        provider_limits=provider_limits,
        today_by_model=today_by_model,
    )
