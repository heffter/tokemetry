"""Stale-source alert kind tests (Task 68.2).

Two layers: the ``evaluate_stale_sources`` evaluator (per-source-type and
explicit thresholds with clock injection, revoked exclusion, source-name
filtering, never-ingested-from-first-seen, content), and the engine's
per-source firing-state machine (independent firing, per-source cooldown,
resolve-on-recovery, and end-to-end recording into ``alert_events``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.db import models
from tokemetry_server.services.alerting.engine import AlertEngine
from tokemetry_server.services.alerting.notifiers import Notifier
from tokemetry_server.services.alerting.rules import evaluate_stale_sources

_NOW = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)


def _rule(**kwargs: object) -> models.AlertRule:
    defaults: dict[str, object] = {
        "name": "stale-source",
        "kind": "stale_source",
        "channels": ["ntfy"],
        "cooldown_seconds": 0,
        "config": {},
    }
    defaults.update(kwargs)
    return models.AlertRule(**defaults)


def _source(
    name: str,
    *,
    source_type: str = "collector",
    last_successful_ingest: datetime | None = None,
    first_seen: datetime | None = None,
    revoked: bool = False,
) -> models.Source:
    """Build a source row; ``first_seen`` defaults far enough back to be stale."""
    seen = first_seen if first_seen is not None else _NOW - timedelta(days=1)
    return models.Source(
        type=source_type,
        name=name,
        first_seen=seen,
        last_seen=_NOW,
        last_successful_ingest=last_successful_ingest,
        revoked=revoked,
    )


class _FakeNotifier(Notifier):
    """An ntfy stub that records what it was asked to deliver."""

    name = "ntfy"

    def __init__(self, ok: bool = True) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self._ok = ok

    def is_configured(self) -> bool:
        return True

    async def send(self, title: str, body: str, severity: str = "info") -> bool:
        self.sent.append((title, body, severity))
        return self._ok


async def _event_count(session: AsyncSession) -> int:
    return int(
        (await session.execute(select(func.count()).select_from(models.AlertEvent))).scalar_one()
    )


# --------------------------------------------------------------------------- #
# Evaluator: thresholds and clock injection
# --------------------------------------------------------------------------- #

async def test_warns_past_type_threshold(async_session: AsyncSession) -> None:
    # Collector default staleness threshold is 30 min; 40 min stale -> warning.
    async_session.add(
        _source("box", last_successful_ingest=_NOW - timedelta(minutes=40))
    )
    await async_session.commit()

    findings = await evaluate_stale_sources(async_session, _rule(), _NOW)

    assert len(findings) == 1
    assert findings[0].finding.severity == "warning"


async def test_critical_past_default_crit(async_session: AsyncSession) -> None:
    # Default crit is 4x warn = 120 min; 130 min stale -> critical.
    async_session.add(
        _source("box", last_successful_ingest=_NOW - timedelta(minutes=130))
    )
    await async_session.commit()

    findings = await evaluate_stale_sources(async_session, _rule(), _NOW)

    assert len(findings) == 1
    assert findings[0].finding.severity == "critical"


async def test_silent_when_fresh(async_session: AsyncSession) -> None:
    async_session.add(
        _source("box", last_successful_ingest=_NOW - timedelta(minutes=5))
    )
    await async_session.commit()

    assert await evaluate_stale_sources(async_session, _rule(), _NOW) == []


async def test_explicit_thresholds_with_clock_injection(
    async_session: AsyncSession,
) -> None:
    # Explicit warn 15 / crit 45 override the per-type defaults.
    async_session.add(
        _source("box", last_successful_ingest=_NOW - timedelta(minutes=60))
    )
    await async_session.commit()
    rule = _rule(warn_threshold=Decimal("15"), crit_threshold=Decimal("45"))

    # 20 min after last ingest -> warning; 50 min -> critical (inject the clock).
    warn = await evaluate_stale_sources(
        async_session, rule, _NOW - timedelta(minutes=40)
    )
    crit = await evaluate_stale_sources(
        async_session, rule, _NOW - timedelta(minutes=10)
    )

    assert warn[0].finding.severity == "warning"
    assert crit[0].finding.severity == "critical"


async def test_gateway_threshold_is_shorter(async_session: AsyncSession) -> None:
    # A gateway (10 min default) 15 min stale warns; a collector would not.
    async_session.add(
        _source(
            "proxy",
            source_type="gateway",
            last_successful_ingest=_NOW - timedelta(minutes=15),
        )
    )
    await async_session.commit()

    findings = await evaluate_stale_sources(async_session, _rule(), _NOW)

    assert len(findings) == 1
    assert findings[0].finding.severity == "warning"


# --------------------------------------------------------------------------- #
# Evaluator: per-source isolation, revocation, filters, never-ingested
# --------------------------------------------------------------------------- #

async def test_per_source_isolation(async_session: AsyncSession) -> None:
    stale = _source("stale-box", last_successful_ingest=_NOW - timedelta(minutes=90))
    fresh = _source("fresh-box", last_successful_ingest=_NOW - timedelta(minutes=2))
    async_session.add_all([stale, fresh])
    await async_session.commit()

    findings = await evaluate_stale_sources(async_session, _rule(), _NOW)

    assert {f.key for f in findings} == {str(stale.id)}


async def test_revoked_source_excluded(async_session: AsyncSession) -> None:
    async_session.add(
        _source(
            "gone",
            last_successful_ingest=_NOW - timedelta(hours=5),
            revoked=True,
        )
    )
    await async_session.commit()

    assert await evaluate_stale_sources(async_session, _rule(), _NOW) == []


async def test_source_name_filter(async_session: AsyncSession) -> None:
    a = _source("watch-me", last_successful_ingest=_NOW - timedelta(minutes=90))
    b = _source("ignore-me", last_successful_ingest=_NOW - timedelta(minutes=90))
    async_session.add_all([a, b])
    await async_session.commit()

    findings = await evaluate_stale_sources(
        async_session,
        _rule(config={"filters": {"source": ["watch-me"]}}),
        _NOW,
    )

    assert {f.key for f in findings} == {str(a.id)}
    assert findings[0].finding.context["scoped_dimensions"] == ["source"]


async def test_never_ingested_measures_from_first_seen(
    async_session: AsyncSession,
) -> None:
    # Never a successful ingest: staleness is measured from first sighting.
    stale = _source(
        "never-ok",
        last_successful_ingest=None,
        first_seen=_NOW - timedelta(minutes=40),
    )
    brand_new = _source(
        "just-here",
        last_successful_ingest=None,
        first_seen=_NOW - timedelta(minutes=1),
    )
    async_session.add_all([stale, brand_new])
    await async_session.commit()

    findings = await evaluate_stale_sources(async_session, _rule(), _NOW)

    assert {f.key for f in findings} == {str(stale.id)}
    assert findings[0].finding.context["last_successful_ingest"] is None


async def test_finding_context_carries_identity(async_session: AsyncSession) -> None:
    src = _source("box", last_successful_ingest=_NOW - timedelta(minutes=45))
    async_session.add(src)
    await async_session.commit()

    findings = await evaluate_stale_sources(async_session, _rule(), _NOW)

    context = findings[0].finding.context
    assert context["source_id"] == src.id
    assert context["source_type"] == "collector"
    assert context["source_name"] == "box"
    assert context["stale_minutes"] == 45.0
    assert context["scoped_dimensions"] == []


# --------------------------------------------------------------------------- #
# Engine: per-source firing state machine, end to end into alert_events
# --------------------------------------------------------------------------- #

async def test_engine_fires_and_records_per_source(async_session: AsyncSession) -> None:
    notifier = _FakeNotifier()
    engine = AlertEngine({"ntfy": notifier})
    async_session.add(_rule())
    async_session.add_all(
        [
            _source("box-a", last_successful_ingest=_NOW - timedelta(minutes=90)),
            _source("box-b", last_successful_ingest=_NOW - timedelta(minutes=90)),
        ]
    )
    await async_session.commit()

    fired = await engine.run(async_session, now=_NOW)
    await async_session.commit()

    # One alert per stale source, each delivered and recorded.
    assert len(fired) == 2
    assert all(item.delivered for item in fired)
    assert len(notifier.sent) == 2
    assert await _event_count(async_session) == 2


async def test_engine_per_source_cooldown_is_independent(
    async_session: AsyncSession,
) -> None:
    notifier = _FakeNotifier()
    engine = AlertEngine({"ntfy": notifier})
    async_session.add(_rule(cooldown_seconds=3600))
    box_a = _source("box-a", last_successful_ingest=_NOW - timedelta(minutes=90))
    box_b = _source("box-b", last_successful_ingest=_NOW - timedelta(minutes=2))
    async_session.add_all([box_a, box_b])
    await async_session.commit()

    first = await engine.run(async_session, now=_NOW)
    await async_session.commit()
    assert len(first) == 1  # only box-a is stale at _NOW

    # box-b goes stale; box-a is still stale but inside its cooldown.
    box_b.last_successful_ingest = _NOW - timedelta(minutes=92)
    await async_session.commit()
    second = await engine.run(async_session, now=_NOW + timedelta(minutes=5))
    await async_session.commit()

    # box-a suppressed by its own cooldown; box-b fires for the first time.
    assert len(second) == 1
    assert second[0].finding.context["source_name"] == "box-b"
    assert await _event_count(async_session) == 2


async def test_engine_resolves_on_recovery(async_session: AsyncSession) -> None:
    notifier = _FakeNotifier()
    engine = AlertEngine({"ntfy": notifier})
    rule = _rule(cooldown_seconds=0)
    async_session.add(rule)
    box = _source("box", last_successful_ingest=_NOW - timedelta(minutes=90))
    async_session.add(box)
    await async_session.commit()

    firing = await engine.run(async_session, now=_NOW)
    await async_session.commit()
    assert firing[0].finding.severity == "warning"

    # The source ingests again: condition clears -> one resolved notice.
    later = _NOW + timedelta(minutes=10)
    box.last_successful_ingest = later - timedelta(minutes=1)
    await async_session.commit()
    resolved = await engine.run(async_session, now=later)
    await async_session.commit()

    assert len(resolved) == 1
    assert resolved[0].finding.severity == "info"
    assert resolved[0].finding.title.startswith("Resolved")
    assert rule.entity_state[str(box.id)]["state"] == "normal"


async def test_engine_revoked_source_clears_without_recovery_notice(
    async_session: AsyncSession,
) -> None:
    notifier = _FakeNotifier()
    engine = AlertEngine({"ntfy": notifier})
    rule = _rule(cooldown_seconds=0)
    async_session.add(rule)
    box = _source("box", last_successful_ingest=_NOW - timedelta(minutes=90))
    async_session.add(box)
    await async_session.commit()

    await engine.run(async_session, now=_NOW)
    await async_session.commit()
    assert await _event_count(async_session) == 1

    # Revoking a source is not a recovery: firing state clears, no new event.
    box.revoked = True
    await async_session.commit()
    result = await engine.run(async_session, now=_NOW + timedelta(minutes=5))
    await async_session.commit()

    assert result == []
    assert await _event_count(async_session) == 1
    assert str(box.id) not in (rule.entity_state or {})


def test_api_accepts_stale_source_rule_with_filter(
    client: TestClient, auth: dict[str, str]
) -> None:
    """The CRUD API must accept ``stale_source`` and persist its source filter."""
    response = client.post(
        "/api/v1/alerts",
        json={
            "name": "watch collectors",
            "kind": "stale_source",
            "channels": ["ntfy"],
            "config": {"filters": {"source": ["box-a"]}},
        },
        headers=auth,
    )

    assert response.status_code == 201, response.text
    assert response.json()["config"]["filters"]["source"] == ["box-a"]
