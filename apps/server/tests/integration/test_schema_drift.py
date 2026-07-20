"""Schema-drift alert tests (Task 68.5).

The ``schema_drift`` kind fires one alert per source whose last reported batch
``schema_version`` is outside the server-supported set, or whose rolling
validation-rejection count (Task 63.2 source health) crosses the threshold.
Covers version-set comparison, rejection thresholds, per-source state,
revoked-source exclusion, and -- through the engine -- a source posting
schema_version 3 firing once with correct context, then resolving after it
returns to a supported version.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.db import models
from tokemetry_server.services.alerting.engine import AlertEngine
from tokemetry_server.services.alerting.notifiers import Notifier
from tokemetry_server.services.alerting.rules import evaluate_schema_drift

_NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


def _rule(**kwargs: object) -> models.AlertRule:
    defaults: dict[str, object] = {
        "name": "schema-drift",
        "kind": "schema_drift",
        "channels": ["ntfy"],
        "cooldown_seconds": 0,
        "config": {},
    }
    defaults.update(kwargs)
    return models.AlertRule(**defaults)


def _source(
    name: str,
    *,
    source_type: str = "gateway",
    reported_schema_version: int | None = 2,
    recent_error_count: int = 0,
    revoked: bool = False,
) -> models.Source:
    return models.Source(
        type=source_type,
        name=name,
        first_seen=_NOW - timedelta(days=1),
        last_seen=_NOW,
        reported_schema_version=reported_schema_version,
        recent_error_count=recent_error_count,
        revoked=revoked,
    )


class _FakeNotifier(Notifier):
    name = "ntfy"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def is_configured(self) -> bool:
        return True

    async def send(self, title: str, body: str, severity: str = "info") -> bool:
        self.sent.append((title, body, severity))
        return True


async def _event_count(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(models.AlertEvent))
    return int(result.scalar_one())


# --------------------------------------------------------------------------- #
# Evaluator: version comparison
# --------------------------------------------------------------------------- #

async def test_unsupported_version_is_critical(async_session: AsyncSession) -> None:
    async_session.add(_source("proxy", reported_schema_version=3))
    await async_session.commit()

    findings = await evaluate_schema_drift(async_session, _rule(), _NOW)

    assert len(findings) == 1
    finding = findings[0].finding
    assert finding.severity == "critical"
    assert finding.context["reported_schema_version"] == 3
    assert finding.context["supported_schema_versions"] == [2]
    assert finding.context["version_drift"] is True


async def test_supported_version_no_rejections_is_silent(
    async_session: AsyncSession,
) -> None:
    async_session.add(_source("proxy", reported_schema_version=2, recent_error_count=0))
    await async_session.commit()

    assert await evaluate_schema_drift(async_session, _rule(), _NOW) == []


async def test_null_reported_version_is_not_drift(async_session: AsyncSession) -> None:
    # A source that has not reported a version yet must not be flagged.
    async_session.add(_source("new", reported_schema_version=None))
    await async_session.commit()

    assert await evaluate_schema_drift(async_session, _rule(), _NOW) == []


# --------------------------------------------------------------------------- #
# Evaluator: rejection thresholds
# --------------------------------------------------------------------------- #

async def test_rejections_warn_and_crit(async_session: AsyncSession) -> None:
    # Defaults warn 5 / crit 20 on a supported version.
    async_session.add(_source("warned", reported_schema_version=2, recent_error_count=8))
    async_session.add(_source("critical", reported_schema_version=2, recent_error_count=25))
    await async_session.commit()

    findings = {f.key: f.finding for f in await evaluate_schema_drift(async_session, _rule(), _NOW)}

    by_name = {f.context["source_name"]: f for f in findings.values()}
    assert by_name["warned"].severity == "warning"
    assert by_name["critical"].severity == "critical"
    assert by_name["warned"].context["version_drift"] is False


async def test_rejections_below_threshold_is_silent(
    async_session: AsyncSession,
) -> None:
    async_session.add(_source("quiet", reported_schema_version=2, recent_error_count=2))
    await async_session.commit()

    assert await evaluate_schema_drift(async_session, _rule(), _NOW) == []


async def test_explicit_rejection_thresholds(async_session: AsyncSession) -> None:
    async_session.add(_source("s", reported_schema_version=2, recent_error_count=3))
    await async_session.commit()

    finding = await evaluate_schema_drift(
        async_session,
        _rule(warn_threshold=Decimal("2"), crit_threshold=Decimal("10")),
        _NOW,
    )
    assert len(finding) == 1
    assert finding[0].finding.severity == "warning"


# --------------------------------------------------------------------------- #
# Evaluator: per-source isolation, revoked, filter, DQ link
# --------------------------------------------------------------------------- #

async def test_per_source_isolation(async_session: AsyncSession) -> None:
    drift = _source("drifter", reported_schema_version=3)
    ok = _source("healthy", reported_schema_version=2, recent_error_count=0)
    async_session.add_all([drift, ok])
    await async_session.commit()

    findings = await evaluate_schema_drift(async_session, _rule(), _NOW)

    assert {f.key for f in findings} == {str(drift.id)}


async def test_revoked_source_excluded(async_session: AsyncSession) -> None:
    async_session.add(_source("gone", reported_schema_version=3, revoked=True))
    await async_session.commit()

    assert await evaluate_schema_drift(async_session, _rule(), _NOW) == []


async def test_source_name_filter(async_session: AsyncSession) -> None:
    a = _source("watch", reported_schema_version=3)
    b = _source("ignore", reported_schema_version=3)
    async_session.add_all([a, b])
    await async_session.commit()

    findings = await evaluate_schema_drift(
        async_session, _rule(config={"filters": {"source": ["watch"]}}), _NOW
    )

    assert {f.key for f in findings} == {str(a.id)}
    assert findings[0].finding.context["scoped_dimensions"] == ["source"]


async def test_context_links_open_data_quality(async_session: AsyncSession) -> None:
    async_session.add(_source("proxy", reported_schema_version=3))
    async_session.add(
        models.DataQualityEvent(
            kind="schema_drift", subject="source:1", detail={}, ts=_NOW, resolved=False
        )
    )
    await async_session.commit()

    findings = await evaluate_schema_drift(async_session, _rule(), _NOW)

    assert findings[0].finding.context["open_data_quality_events"] == 1


# --------------------------------------------------------------------------- #
# Engine: fire once with context, resolve on recovery
# --------------------------------------------------------------------------- #

async def test_engine_fires_once_and_resolves(async_session: AsyncSession) -> None:
    notifier = _FakeNotifier()
    engine = AlertEngine({"ntfy": notifier})
    rule = _rule(cooldown_seconds=0)
    async_session.add(rule)
    proxy = _source("proxy-9", reported_schema_version=3)
    async_session.add(proxy)
    await async_session.commit()

    fired = await engine.run(async_session, now=_NOW)
    await async_session.commit()

    assert len(fired) == 1
    assert fired[0].finding.severity == "critical"
    assert fired[0].finding.context["reported_schema_version"] == 3
    assert await _event_count(async_session) == 1

    # The source upgrades/downgrades back to a supported version, no rejections.
    proxy.reported_schema_version = 2
    proxy.recent_error_count = 0
    await async_session.commit()
    resolved = await engine.run(async_session, now=_NOW + timedelta(minutes=5))
    await async_session.commit()

    assert len(resolved) == 1
    assert resolved[0].finding.severity == "info"
    assert resolved[0].finding.title.startswith("Resolved")
    assert "supported schema" in resolved[0].finding.body
    assert rule.entity_state is not None
    assert rule.entity_state[str(proxy.id)]["state"] == "normal"


async def test_engine_api_accepts_schema_drift_kind() -> None:
    from tokemetry_server.services.alerting.rules import ALL_EVALUATOR_KINDS, is_grouped_kind

    assert "schema_drift" in ALL_EVALUATOR_KINDS
    assert is_grouped_kind("schema_drift")
