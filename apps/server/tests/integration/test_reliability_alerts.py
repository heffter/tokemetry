"""Reliability alert tests (Task 68.4).

The three sliding-window reliability kinds over final attempts:

- ``failure_rate`` -- failed-attempt share, with a minimum-sample-size guard.
- ``latency_p95`` -- p95 of ``latency_ms`` (a property test proves it is the
  percentile, not the max).
- ``fallback_rate`` -- share of *logical requests* (not attempts) that fell
  back, so a multi-attempt fallback counts once.

Covers empty/below-minimum windows, warn/crit transitions, dimension-filter
scoping, per-rule window/min_samples config, and an engine smoke test that a
finding fires and records into ``alert_events``.
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
from tokemetry_server.services.alerting.rules import evaluate_rule

_NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
_RECENT = _NOW - timedelta(minutes=10)


def _rule(kind: str, **kwargs: object) -> models.AlertRule:
    defaults: dict[str, object] = {"name": kind, "kind": kind, "cooldown_seconds": 0, "config": {}}
    defaults.update(kwargs)
    return models.AlertRule(**defaults)


def _attempt(
    session: AsyncSession,
    event_id: str,
    *,
    provider: str = "anthropic",
    model: str = "m",
    success: bool = True,
    latency_ms: int | None = None,
    ts: datetime = _RECENT,
    **fields: object,
) -> None:
    """Add a final-attempt ledger row with explicit success/latency."""
    session.add(
        models.UsageEventV2(
            provider=provider,
            event_id=event_id,
            event_kind="attempt",
            finality="final",
            native_model=model,
            ts_started=ts,
            provenance="local_estimate",
            success=success,
            latency_ms=latency_ms,
            **fields,
        )
    )


def _logical_request(
    session: AsyncSession,
    request_id: str,
    *,
    provider: str = "anthropic",
    requested_model: str = "m",
    fallback_count: int = 0,
    ts: datetime = _RECENT,
) -> None:
    """Add a logical-request row with a fallback count."""
    session.add(
        models.LogicalRequest(
            provider=provider,
            logical_request_id=request_id,
            requested_model=requested_model,
            fallback_count=fallback_count,
            ts_first=ts,
            ts_last=ts,
        )
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


# --------------------------------------------------------------------------- #
# failure_rate
# --------------------------------------------------------------------------- #

async def test_failure_rate_warns_and_crits(async_session: AsyncSession) -> None:
    # 20 attempts; vary failures to cross warn (10%) then crit (25%).
    for i in range(20):
        _attempt(async_session, f"w{i}", success=i >= 3)  # 3/20 = 15% -> warning
    await async_session.commit()
    warn = await evaluate_rule(async_session, _rule("failure_rate"), _NOW)
    assert warn is not None
    assert warn.severity == "warning"
    assert warn.context["failure_rate_pct"] == 15.0
    assert warn.context["sample_size"] == 20


async def test_failure_rate_critical(async_session: AsyncSession) -> None:
    for i in range(20):
        _attempt(async_session, f"c{i}", success=i >= 6)  # 6/20 = 30% -> critical
    await async_session.commit()
    finding = await evaluate_rule(async_session, _rule("failure_rate"), _NOW)
    assert finding is not None
    assert finding.severity == "critical"


async def test_failure_rate_below_min_samples_is_silent(
    async_session: AsyncSession,
) -> None:
    # 5 attempts, all failed (100%), but below the default 20-sample floor.
    for i in range(5):
        _attempt(async_session, f"s{i}", success=False)
    await async_session.commit()
    assert await evaluate_rule(async_session, _rule("failure_rate"), _NOW) is None


async def test_failure_rate_empty_window_is_silent(
    async_session: AsyncSession,
) -> None:
    assert await evaluate_rule(async_session, _rule("failure_rate"), _NOW) is None


async def test_failure_rate_respects_provider_filter(
    async_session: AsyncSession,
) -> None:
    for i in range(20):
        _attempt(async_session, f"a{i}", provider="anthropic", success=i >= 2)  # 10%
    for i in range(20):
        _attempt(async_session, f"o{i}", provider="openai", success=False)  # 100%
    await async_session.commit()

    scoped = await evaluate_rule(
        async_session,
        _rule("failure_rate", config={"filters": {"provider": ["anthropic"]}}),
        _NOW,
    )
    assert scoped is not None
    assert scoped.context["failure_rate_pct"] == 10.0
    assert scoped.context["scoped_dimensions"] == ["provider"]


async def test_failure_rate_min_samples_config_override(
    async_session: AsyncSession,
) -> None:
    for i in range(5):
        _attempt(async_session, f"m{i}", success=i >= 1)  # 1/5 = 20%
    await async_session.commit()
    finding = await evaluate_rule(
        async_session, _rule("failure_rate", config={"min_samples": 5}), _NOW
    )
    assert finding is not None
    assert finding.context["sample_size"] == 5


# --------------------------------------------------------------------------- #
# latency_p95
# --------------------------------------------------------------------------- #

async def test_latency_p95_fires_above_threshold(async_session: AsyncSession) -> None:
    for i in range(20):
        _attempt(async_session, f"l{i}", latency_ms=40_000)
    await async_session.commit()
    finding = await evaluate_rule(async_session, _rule("latency_p95"), _NOW)
    assert finding is not None
    assert finding.context["latency_p95_ms"] == 40_000.0


async def test_latency_p95_is_percentile_not_max(async_session: AsyncSession) -> None:
    # 19 fast + 1 huge outlier: p95 (rank 19 of 20) excludes the outlier.
    for i in range(19):
        _attempt(async_session, f"f{i}", latency_ms=1_000)
    _attempt(async_session, "outlier", latency_ms=100_000)
    await async_session.commit()
    finding = await evaluate_rule(
        async_session,
        _rule("latency_p95", warn_threshold=Decimal("500")),
        _NOW,
    )
    assert finding is not None
    assert finding.context["latency_p95_ms"] == 1_000.0  # not 100000


async def test_latency_p95_ignores_null_latency_and_min_samples(
    async_session: AsyncSession,
) -> None:
    # Only 5 attempts recorded a latency -> below the 20-sample floor.
    for i in range(5):
        _attempt(async_session, f"n{i}", latency_ms=50_000)
    for i in range(20):
        _attempt(async_session, f"z{i}", latency_ms=None)
    await async_session.commit()
    assert await evaluate_rule(async_session, _rule("latency_p95"), _NOW) is None


# --------------------------------------------------------------------------- #
# fallback_rate
# --------------------------------------------------------------------------- #

async def test_fallback_rate_uses_logical_requests_as_denominator(
    async_session: AsyncSession,
) -> None:
    # One request fell back over 3 attempts; one did not. Denominator is 2
    # (requests), not 4 (attempts) -- the property under test.
    _logical_request(async_session, "r1", fallback_count=3)
    _logical_request(async_session, "r2", fallback_count=0)
    await async_session.commit()
    finding = await evaluate_rule(
        async_session, _rule("fallback_rate", config={"min_samples": 1}), _NOW
    )
    assert finding is not None
    assert finding.context["sample_size"] == 2
    assert finding.context["fallback_rate_pct"] == 50.0


async def test_fallback_rate_warns(async_session: AsyncSession) -> None:
    for i in range(20):
        _logical_request(async_session, f"rq{i}", fallback_count=1 if i < 4 else 0)  # 20%
    await async_session.commit()
    finding = await evaluate_rule(async_session, _rule("fallback_rate"), _NOW)
    assert finding is not None
    assert finding.severity == "warning"
    assert finding.context["fallback_rate_pct"] == 20.0


async def test_fallback_rate_below_min_samples_is_silent(
    async_session: AsyncSession,
) -> None:
    _logical_request(async_session, "only", fallback_count=1)
    await async_session.commit()
    assert await evaluate_rule(async_session, _rule("fallback_rate"), _NOW) is None


async def test_fallback_rate_provider_filter(async_session: AsyncSession) -> None:
    for i in range(20):
        _logical_request(async_session, f"a{i}", provider="anthropic", fallback_count=0)
    for i in range(20):
        _logical_request(async_session, f"o{i}", provider="openai", fallback_count=1)
    await async_session.commit()

    scoped = await evaluate_rule(
        async_session,
        _rule("fallback_rate", config={"filters": {"provider": ["openai"]}}),
        _NOW,
    )
    assert scoped is not None
    assert scoped.context["fallback_rate_pct"] == 100.0
    assert scoped.context["scoped_dimensions"] == ["provider"]


async def test_fallback_rate_ignores_out_of_window(async_session: AsyncSession) -> None:
    # Default window is 60m; place all requests 2h ago -> none counted.
    old = _NOW - timedelta(hours=2)
    for i in range(20):
        _logical_request(async_session, f"old{i}", fallback_count=1, ts=old)
    await async_session.commit()
    assert await evaluate_rule(async_session, _rule("fallback_rate"), _NOW) is None


# --------------------------------------------------------------------------- #
# Engine smoke
# --------------------------------------------------------------------------- #

async def test_reliability_kind_fires_through_engine(
    async_session: AsyncSession,
) -> None:
    notifier = _FakeNotifier()
    engine = AlertEngine({"ntfy": notifier})
    async_session.add(_rule("failure_rate", channels=["ntfy"]))
    for i in range(20):
        _attempt(async_session, f"e{i}", success=i >= 6)  # 30% -> critical
    await async_session.commit()

    fired = await engine.run(async_session, now=_NOW)
    await async_session.commit()

    assert len(fired) == 1
    assert fired[0].delivered is True
    count = (
        await async_session.execute(select(func.count()).select_from(models.AlertEvent))
    ).scalar_one()
    assert int(count) == 1


# --------------------------------------------------------------------------- #
# API validation of the new kinds and config
# --------------------------------------------------------------------------- #

def test_api_accepts_reliability_rule_with_window_config(
    client: TestClient, auth: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/alerts",
        json={
            "name": "anthropic failures",
            "kind": "failure_rate",
            "config": {
                "filters": {"provider": ["anthropic"]},
                "window_minutes": 30,
                "min_samples": 50,
            },
        },
        headers=auth,
    )
    assert response.status_code == 201, response.text
    config = response.json()["config"]
    assert config["window_minutes"] == 30
    assert config["min_samples"] == 50


def test_api_rejects_non_positive_window(client: TestClient, auth: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/alerts",
        json={"name": "bad-window", "kind": "latency_p95", "config": {"window_minutes": 0}},
        headers=auth,
    )
    assert response.status_code == 422
