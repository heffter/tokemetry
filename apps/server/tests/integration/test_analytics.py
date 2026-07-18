"""Integration tests for the time-based analytics engines."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from conftest import make_v1_event
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.db import models
from tokemetry_server.services import analytics

_NOW = datetime(2026, 7, 9, 16, 30, 0, tzinfo=UTC)


async def _add_event(
    session: AsyncSession,
    event_id: str,
    ts: datetime,
    tokens: int,
    cost: Decimal | None = None,
) -> None:
    session.add(
        make_v1_event(
            provider="anthropic",
            event_id=event_id,
            ts=ts,
            model="claude-opus-4-5",
            machine="box-1",
            input_tokens=tokens,
            cost_usd=cost,
            provenance="local_estimate",
        )
    )


async def _add_snapshot(
    session: AsyncSession,
    ts: datetime,
    utilization: float,
    window_kind: str = "five_hour",
    resets_at: datetime | None = None,
) -> None:
    session.add(
        models.LimitSnapshot(
            provider="anthropic",
            machine="box-1",
            ts=ts,
            window_kind=window_kind,
            utilization_pct=utilization,
            resets_at=resets_at,
            provenance="official",
        )
    )


async def test_current_limits_returns_latest(async_session: AsyncSession) -> None:
    await _add_snapshot(async_session, _NOW - timedelta(minutes=30), 10.0)
    await _add_snapshot(async_session, _NOW, 25.0)
    await _add_snapshot(async_session, _NOW, 5.0, window_kind="seven_day")
    await async_session.commit()

    limits = await analytics.current_limits(async_session)

    by_kind = {limit.window_kind: limit for limit in limits}
    assert float(by_kind["five_hour"].utilization_pct) == 25.0
    assert set(by_kind) == {"five_hour", "seven_day"}


async def test_token_burn_rate(async_session: AsyncSession) -> None:
    await _add_event(async_session, "e1", _NOW - timedelta(minutes=10), 600)
    await _add_event(async_session, "e2", _NOW - timedelta(minutes=5), 600)
    await _add_event(async_session, "old", _NOW - timedelta(hours=3), 99999)
    await async_session.commit()

    rate = await analytics.token_burn_rate(async_session, now=_NOW, window_minutes=60)

    assert rate == 1200 / 60  # only the two recent events, over 60 minutes


async def test_predict_exhaustion_from_slope(async_session: AsyncSession) -> None:
    await _add_snapshot(async_session, _NOW - timedelta(minutes=60), 40.0)
    await _add_snapshot(async_session, _NOW, 70.0)
    await async_session.commit()

    prediction = await analytics.predict_exhaustion(async_session, now=_NOW)

    assert prediction is not None
    assert prediction.slope_pct_per_min == 0.5  # 30% over 60 min
    assert prediction.predicted_exhaustion_at == _NOW + timedelta(minutes=60)


async def test_predict_returns_none_without_two_points(async_session: AsyncSession) -> None:
    await _add_snapshot(async_session, _NOW, 40.0)
    await async_session.commit()
    assert await analytics.predict_exhaustion(async_session, now=_NOW) is None


async def test_blocks_anchored_to_reset(async_session: AsyncSession) -> None:
    anchor_reset = datetime(2026, 7, 9, 15, 0, 0, tzinfo=UTC)
    await _add_snapshot(async_session, _NOW, 50.0, resets_at=anchor_reset)
    # Two events in the same minute of block 0, one in the prior block.
    await _add_event(async_session, "a", datetime(2026, 7, 9, 16, 0, 0, tzinfo=UTC), 100)
    await _add_event(async_session, "b", datetime(2026, 7, 9, 16, 0, 30, tzinfo=UTC), 200)
    await _add_event(async_session, "c", datetime(2026, 7, 9, 14, 0, 0, tzinfo=UTC), 50)
    await async_session.commit()

    blocks = await analytics.blocks(
        async_session, _NOW - timedelta(hours=8), _NOW + timedelta(hours=1)
    )

    assert len(blocks) == 2
    latest = blocks[-1]
    assert latest.start == anchor_reset
    assert latest.total_tokens == 300
    assert latest.peak_tokens_per_min == 300  # both events in the same minute
    assert latest.end_utilization_pct == 50.0
