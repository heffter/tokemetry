"""Tests for the alert engine: dispatch, cooldown, quiet hours, history."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.db import models
from tokemetry_server.services.alerting.engine import AlertEngine
from tokemetry_server.services.alerting.notifiers import Notifier

_NOW = datetime(2026, 7, 9, 16, 0, 0, tzinfo=UTC)


class _FakeNotifier(Notifier):
    name = "fake"

    def __init__(self, ok: bool = True) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self._ok = ok

    def is_configured(self) -> bool:
        return True

    async def send(self, title: str, body: str, severity: str = "info") -> bool:
        self.sent.append((title, body, severity))
        return self._ok


async def _add_rule(session: AsyncSession, **kwargs: object) -> models.AlertRule:
    defaults: dict[str, object] = {
        "name": "opus-limit",
        "kind": "limit_pct",
        "window_kind": "five_hour",
        "threshold": Decimal("80"),
        "channels": ["fake"],
        "cooldown_seconds": 3600,
        "enabled": True,
    }
    defaults.update(kwargs)
    rule = models.AlertRule(**defaults)
    session.add(rule)
    await session.flush()
    return rule


async def _add_snapshot(
    session: AsyncSession, util: float, ts: datetime | None = None
) -> None:
    session.add(
        models.LimitSnapshot(
            provider="anthropic",
            ts=ts if ts is not None else _NOW,
            window_kind="five_hour",
            utilization_pct=util,
            provenance="official",
        )
    )


async def _event_count(session: AsyncSession) -> int:
    return int(
        (await session.execute(select(func.count()).select_from(models.AlertEvent))).scalar_one()
    )


async def test_fires_dispatches_and_records(async_session: AsyncSession) -> None:
    notifier = _FakeNotifier()
    engine = AlertEngine({"fake": notifier})
    await _add_rule(async_session)
    await _add_snapshot(async_session, 90.0)
    await async_session.commit()

    fired = await engine.run(async_session, now=_NOW)
    await async_session.commit()

    assert len(fired) == 1
    assert fired[0].delivered is True
    assert len(notifier.sent) == 1
    assert await _event_count(async_session) == 1


async def test_cooldown_suppresses_second_fire(async_session: AsyncSession) -> None:
    notifier = _FakeNotifier()
    engine = AlertEngine({"fake": notifier})
    await _add_rule(async_session, cooldown_seconds=3600)
    await _add_snapshot(async_session, 90.0)
    await async_session.commit()

    await engine.run(async_session, now=_NOW)
    await async_session.commit()
    second = await engine.run(async_session, now=_NOW + timedelta(minutes=5))
    await async_session.commit()

    assert second == []
    assert await _event_count(async_session) == 1


async def test_cooldown_expires(async_session: AsyncSession) -> None:
    engine = AlertEngine({"fake": _FakeNotifier()})
    await _add_rule(async_session, cooldown_seconds=60)
    await _add_snapshot(async_session, 90.0)
    await async_session.commit()

    await engine.run(async_session, now=_NOW)
    await async_session.commit()
    later = await engine.run(async_session, now=_NOW + timedelta(minutes=10))
    await async_session.commit()

    assert len(later) == 1
    assert await _event_count(async_session) == 2


async def test_warn_threshold_yields_warning(async_session: AsyncSession) -> None:
    notifier = _FakeNotifier()
    engine = AlertEngine({"fake": notifier})
    await _add_rule(
        async_session,
        threshold=None,
        warn_threshold=Decimal("80"),
        crit_threshold=Decimal("95"),
    )
    await _add_snapshot(async_session, 85.0)
    await async_session.commit()

    fired = await engine.run(async_session, now=_NOW)

    assert fired[0].finding.severity == "warning"
    assert notifier.sent[-1][2] == "warning"


async def test_crit_threshold_yields_critical(async_session: AsyncSession) -> None:
    notifier = _FakeNotifier()
    engine = AlertEngine({"fake": notifier})
    await _add_rule(
        async_session,
        threshold=None,
        warn_threshold=Decimal("80"),
        crit_threshold=Decimal("95"),
    )
    await _add_snapshot(async_session, 97.0)
    await async_session.commit()

    fired = await engine.run(async_session, now=_NOW)

    assert fired[0].finding.severity == "critical"
    assert notifier.sent[-1][2] == "critical"


async def test_resolved_notification_on_recovery(async_session: AsyncSession) -> None:
    notifier = _FakeNotifier()
    engine = AlertEngine({"fake": notifier})
    await _add_rule(
        async_session, threshold=None, warn_threshold=Decimal("80"), crit_threshold=Decimal("95")
    )
    await _add_snapshot(async_session, 90.0)
    await async_session.commit()

    firing = await engine.run(async_session, now=_NOW)
    await async_session.commit()
    assert firing[0].finding.severity == "warning"

    # Utilization drops below threshold in a newer snapshot: condition clears.
    await _add_snapshot(async_session, 10.0, _NOW + timedelta(minutes=10))
    await async_session.commit()
    resolved = await engine.run(async_session, now=_NOW + timedelta(minutes=11))
    await async_session.commit()

    assert len(resolved) == 1
    assert resolved[0].finding.severity == "info"
    assert resolved[0].finding.title.startswith("Resolved")


async def test_channel_helper(async_session: AsyncSession) -> None:
    notifier = _FakeNotifier()
    engine = AlertEngine({"fake": notifier})
    assert await engine.test_channel("fake") is True
    assert await engine.test_channel("missing") is False


async def test_quiet_hours_skip(async_session: AsyncSession) -> None:
    engine = AlertEngine({"fake": _FakeNotifier()})
    # Quiet 22:00-07:00; _NOW is 16:00 so NOT quiet -> fires. Use a window
    # covering 16:00 to prove suppression.
    await _add_rule(async_session, quiet_hours={"start_hour": 15, "end_hour": 18})
    await _add_snapshot(async_session, 90.0)
    await async_session.commit()

    fired = await engine.run(async_session, now=_NOW)

    assert fired == []
    assert await _event_count(async_session) == 0


async def test_records_even_when_no_channel_delivers(async_session: AsyncSession) -> None:
    # No matching notifier configured -> event still recorded, delivered=False.
    engine = AlertEngine({})
    await _add_rule(async_session, channels=["ntfy"])
    await _add_snapshot(async_session, 90.0)
    await async_session.commit()

    fired = await engine.run(async_session, now=_NOW)
    await async_session.commit()

    assert len(fired) == 1
    assert fired[0].delivered is False
    assert await _event_count(async_session) == 1


async def test_disabled_rule_ignored(async_session: AsyncSession) -> None:
    engine = AlertEngine({"fake": _FakeNotifier()})
    await _add_rule(async_session, enabled=False)
    await _add_snapshot(async_session, 90.0)
    await async_session.commit()

    assert await engine.run(async_session, now=_NOW) == []
