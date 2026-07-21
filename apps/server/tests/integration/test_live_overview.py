"""Provider-neutral live overview aggregation (Task 73, service level)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.db import models
from tokemetry_server.services.live_overview import build_live_overview
from tokemetry_server.services.query_framework import QueryFilters

_NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)


def _event(
    event_id: str, provider: str, model: str, ts: datetime, *, output: int
) -> models.UsageEventV2:
    return models.UsageEventV2(
        provider=provider,
        event_id=event_id,
        schema_version=2,
        event_kind="attempt",
        finality="final",
        sequence=0,
        native_model=model,
        ts_started=ts,
        input_tokens=0,
        output_tokens=output,
        cache_read_tokens=0,
        cache_write_short_tokens=0,
        cache_write_long_tokens=0,
        reasoning_tokens=0,
        success=True,
        tool_call_count=0,
        provenance="official",
        dimensions={},
        extra={},
    )


def _limit(provider: str, util: float, remaining: float) -> models.LimitSnapshot:
    return models.LimitSnapshot(
        provider=provider,
        ts=_NOW - timedelta(minutes=1),
        window_kind="five_hour",
        utilization_pct=Decimal(str(util)),
        resets_at=_NOW + timedelta(hours=2),
        provenance="official",
        limit_amount=Decimal("200000"),
        remaining=Decimal(str(remaining)),
        unit="tokens",
        raw={},
    )


async def _seed(session: AsyncSession) -> None:
    recent = _NOW - timedelta(minutes=5)
    session.add(_event("a1", "anthropic", "claude-sonnet-4-5", recent, output=600))
    session.add(_event("a2", "anthropic", "claude-haiku-4-5", recent, output=300))
    session.add(_event("o1", "openai", "gpt-5", recent, output=100))
    # Remaining small enough that the burn depletes it before the 2h reset.
    session.add(_limit("anthropic", 50.0, 5_000))
    await session.commit()


async def test_burn_rate_limits_and_today_by_model(async_session: AsyncSession) -> None:
    await _seed(async_session)
    overview = await build_live_overview(async_session, QueryFilters(), _NOW)

    # Burn: 1000 tokens over the 15-minute window.
    assert overview.burn_rate_per_min == 1000 / 15
    # One provider limit, with a burn-based exhaustion estimate before reset.
    (limit,) = overview.provider_limits
    assert limit.provider == "anthropic"
    assert limit.utilization_pct == 50.0
    assert limit.predicted_exhaustion_at is not None
    assert limit.resets_at is not None
    assert limit.predicted_exhaustion_at <= limit.resets_at
    # Today by model, largest first.
    models_seen = [(m.native_model, m.total_tokens) for m in overview.today_by_model]
    assert models_seen[0] == ("claude-sonnet-4-5", 600)
    assert ("gpt-5", 100) in models_seen


async def test_provider_filter_scopes_burn_and_limits(
    async_session: AsyncSession,
) -> None:
    await _seed(async_session)
    overview = await build_live_overview(
        async_session, QueryFilters(provider="openai"), _NOW
    )
    # Burn and today reflect only OpenAI; anthropic's limit is filtered out.
    assert overview.burn_rate_per_min == 100 / 15
    assert overview.provider_limits == []  # no openai limit snapshot
    assert [m.native_model for m in overview.today_by_model] == ["gpt-5"]


async def test_reset_before_exhaustion_leaves_prediction_none(
    async_session: AsyncSession,
) -> None:
    """When the window resets before the burn would deplete it, no prediction."""
    recent = _NOW - timedelta(minutes=5)
    async_session.add(_event("a1", "anthropic", "claude-sonnet-4-5", recent, output=1))
    # Huge remaining relative to a tiny burn -> depletion far beyond the reset.
    async_session.add(_limit("anthropic", 10.0, 100_000_000))
    await async_session.commit()

    overview = await build_live_overview(async_session, QueryFilters(), _NOW)
    (limit,) = overview.provider_limits
    assert limit.predicted_exhaustion_at is None
