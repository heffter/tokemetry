"""Tests for individual alert rule evaluators."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from conftest import make_v1_event
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.db import models
from tokemetry_server.services.alerting.rules import AlertFinding, evaluate_rule

_NOW = datetime(2026, 7, 9, 16, 0, 0, tzinfo=UTC)


def _rule(kind: str, **kwargs: object) -> models.AlertRule:
    defaults: dict[str, object] = {"name": kind, "kind": kind, "cooldown_seconds": 0}
    defaults.update(kwargs)
    return models.AlertRule(**defaults)


async def _snapshot(
    session: AsyncSession, ts: datetime, util: float, resets_at: datetime | None = None
) -> None:
    session.add(
        models.LimitSnapshot(
            provider="anthropic",
            ts=ts,
            window_kind="five_hour",
            utilization_pct=util,
            resets_at=resets_at,
            provenance="official",
        )
    )


async def test_limit_pct_fires_above_threshold(async_session: AsyncSession) -> None:
    await _snapshot(async_session, _NOW, 85.0)
    await async_session.commit()

    finding = await evaluate_rule(
        async_session,
        _rule("limit_pct", window_kind="five_hour", threshold=Decimal("80")),
        _NOW,
    )

    assert isinstance(finding, AlertFinding)
    assert finding.severity == "warning"


async def test_limit_pct_critical_at_95(async_session: AsyncSession) -> None:
    await _snapshot(async_session, _NOW, 97.0)
    await async_session.commit()

    finding = await evaluate_rule(
        async_session, _rule("limit_pct", window_kind="five_hour", threshold=Decimal("80")), _NOW
    )
    assert finding is not None
    assert finding.severity == "critical"


async def test_limit_pct_silent_below_threshold(async_session: AsyncSession) -> None:
    await _snapshot(async_session, _NOW, 40.0)
    await async_session.commit()
    finding = await evaluate_rule(
        async_session, _rule("limit_pct", window_kind="five_hour", threshold=Decimal("80")), _NOW
    )
    assert finding is None


async def test_predicted_exhaustion(async_session: AsyncSession) -> None:
    reset = _NOW + timedelta(hours=2)
    await _snapshot(async_session, _NOW - timedelta(minutes=60), 40.0, resets_at=reset)
    await _snapshot(async_session, _NOW, 80.0, resets_at=reset)
    await async_session.commit()

    finding = await evaluate_rule(async_session, _rule("predicted_exhaustion"), _NOW)

    assert finding is not None
    assert "before it resets" in finding.body


async def test_burn_rate(async_session: AsyncSession) -> None:
    async_session.add(
        make_v1_event(
            provider="anthropic",
            event_id="e1",
            ts=_NOW - timedelta(minutes=5),
            model="m",
            input_tokens=600_000,
            provenance="local_estimate",
        )
    )
    await async_session.commit()

    finding = await evaluate_rule(
        async_session, _rule("burn_rate", threshold=Decimal("5000")), _NOW
    )
    assert finding is not None


async def test_collector_stale(async_session: AsyncSession) -> None:
    async_session.add(
        models.Machine(id="box-1", last_seen=_NOW - timedelta(hours=2))
    )
    await async_session.commit()

    finding = await evaluate_rule(
        async_session, _rule("collector_stale", threshold=Decimal("30")), _NOW
    )
    assert finding is not None
    assert "box-1" in finding.body


async def test_unknown_model(async_session: AsyncSession) -> None:
    async_session.add(
        make_v1_event(
            provider="anthropic",
            event_id="e1",
            ts=_NOW - timedelta(hours=1),
            model="claude-mystery-9",
            input_tokens=10,
            cost_usd=None,
            provenance="local_estimate",
        )
    )
    await async_session.commit()

    finding = await evaluate_rule(async_session, _rule("unknown_model"), _NOW)
    assert finding is not None


async def test_unknown_kind_raises(async_session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="unknown alert rule kind"):
        await evaluate_rule(async_session, _rule("nonsense"), _NOW)
