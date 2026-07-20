"""End-to-end acceptance for the alerts epic (Task 68.6, PRD Epic TOK-9).

Drives every new and reworked alert kind through the real engine loop and the
real ntfy/Telegram/SMTP notifiers (HTTP mocked, SMTP stubbed) to prove channel
compatibility (FR-ALERT-001), asserts every kind's fired context is content-free
via a shared prohibited-content checker (FR-ALERT-010), and verifies quiet hours
and cooldown on a representative new kind (FR-ALERT-009).
"""

from __future__ import annotations

import smtplib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Literal

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.config import Settings
from tokemetry_server.db import models
from tokemetry_server.services.alerting.engine import AlertEngine
from tokemetry_server.services.alerting.notifiers import Notifier, build_notifiers
from tokemetry_server.services.computed_costs import record_cost

_NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)

# --------------------------------------------------------------------------- #
# Shared content-free checker (FR-ALERT-010)
# --------------------------------------------------------------------------- #

#: Keys that would carry raw usage content; an alert context must never hold one.
_PROHIBITED_KEYS = {
    "prompt",
    "completion",
    "response",
    "content",
    "input_text",
    "output_text",
    "messages",
    "text",
    "body_text",
}


def assert_content_free(value: object, path: str = "context") -> None:
    """Assert a context value is metadata only -- no prohibited keys, primitives only."""
    if isinstance(value, dict):
        for key, item in value.items():
            assert key.lower() not in _PROHIBITED_KEYS, f"prohibited key at {path}.{key}"
            assert_content_free(item, f"{path}.{key}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            assert_content_free(item, f"{path}[{i}]")
    else:
        assert value is None or isinstance(
            value, (str, int, float, bool)
        ), f"non-primitive at {path}: {type(value)}"


# --------------------------------------------------------------------------- #
# Recording notifier registry: real notifiers, mocked transports
# --------------------------------------------------------------------------- #


class _Recorder:
    """Captures what each channel was asked to deliver."""

    def __init__(self) -> None:
        self.http_urls: list[str] = []
        self.smtp_subjects: list[str] = []


def _fake_smtp_class(recorder: _Recorder) -> type:
    class _FakeSMTP:
        def __init__(self, host: str, port: int = 0, timeout: float = 0) -> None:
            self._host = host

        def __enter__(self) -> _FakeSMTP:
            return self

        def __exit__(self, *exc: object) -> Literal[False]:
            return False

        def starttls(self) -> None:
            return None

        def login(self, user: str, password: str) -> None:
            return None

        def send_message(self, message: EmailMessage) -> None:
            recorder.smtp_subjects.append(str(message["Subject"]))

    return _FakeSMTP


@pytest.fixture
def channels(monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, Notifier], _Recorder]:
    """A registry of the real ntfy/Telegram/SMTP notifiers, all configured, recording."""
    recorder = _Recorder()

    def handler(request: httpx.Request) -> httpx.Response:
        recorder.http_urls.append(str(request.url))
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(smtplib, "SMTP", _fake_smtp_class(recorder))
    settings = Settings(
        ntfy_topic="tok",
        telegram_bot_token="1:abc",
        telegram_chat_id="42",
        smtp_host="mail",
        smtp_from="a@b.c",
        smtp_to="d@e.f",
    )
    return build_notifiers(settings, client), recorder


_ALL_CHANNELS = ["ntfy", "telegram", "smtp"]


def _rule(kind: str, **kwargs: object) -> models.AlertRule:
    defaults: dict[str, object] = {
        "name": kind,
        "kind": kind,
        "channels": _ALL_CHANNELS,
        "cooldown_seconds": 0,
        "config": {},
    }
    defaults.update(kwargs)
    return models.AlertRule(**defaults)


# --------------------------------------------------------------------------- #
# Per-kind fixtures: seed the data each kind needs to fire
# --------------------------------------------------------------------------- #


def _attempt(
    session: AsyncSession,
    event_id: str,
    *,
    model: str = "m",
    success: bool = True,
    latency_ms: int | None = None,
) -> None:
    session.add(
        models.UsageEventV2(
            provider="anthropic",
            event_id=event_id,
            event_kind="attempt",
            finality="final",
            native_model=model,
            ts_started=_NOW - timedelta(minutes=10),
            provenance="local_estimate",
            success=success,
            latency_ms=latency_ms,
        )
    )


async def _seed_failure_rate(session: AsyncSession) -> None:
    for i in range(20):
        _attempt(session, f"fr{i}", success=i >= 6)  # 30%


async def _seed_latency_p95(session: AsyncSession) -> None:
    for i in range(20):
        _attempt(session, f"lat{i}", latency_ms=40_000)


async def _seed_fallback_rate(session: AsyncSession) -> None:
    for i in range(20):
        session.add(
            models.LogicalRequest(
                provider="anthropic",
                logical_request_id=f"lr{i}",
                requested_model="m",
                fallback_count=1 if i < 8 else 0,  # 40%
                ts_first=_NOW - timedelta(minutes=5),
                ts_last=_NOW - timedelta(minutes=5),
            )
        )


async def _seed_unpriced_events(session: AsyncSession) -> None:
    _attempt(session, "up1")
    await session.flush()
    await record_cost(
        session, "anthropic", "up1", amount=None, cost_status="unpriced", pricing_version="1"
    )


async def _seed_unknown_model(session: AsyncSession) -> None:
    _attempt(session, "um1", model="brand-new-9")  # no registry row -> unknown


async def _seed_stale_source(session: AsyncSession) -> None:
    session.add(
        models.Source(
            type="collector",
            name="stale-box",
            first_seen=_NOW - timedelta(days=1),
            last_seen=_NOW,
            last_successful_ingest=_NOW - timedelta(minutes=90),
            revoked=False,
        )
    )


async def _seed_schema_drift(session: AsyncSession) -> None:
    session.add(
        models.Source(
            type="gateway",
            name="drift-proxy",
            first_seen=_NOW - timedelta(days=1),
            last_seen=_NOW,
            reported_schema_version=3,
            revoked=False,
        )
    )


_Seeder = Callable[[AsyncSession], Awaitable[None]]

#: Every new/reworked kind (Tasks 68.2-68.5) with its seeder.
_KIND_MATRIX: list[tuple[str, _Seeder]] = [
    ("failure_rate", _seed_failure_rate),
    ("latency_p95", _seed_latency_p95),
    ("fallback_rate", _seed_fallback_rate),
    ("unpriced_events", _seed_unpriced_events),
    ("unknown_model", _seed_unknown_model),
    ("stale_source", _seed_stale_source),
    ("schema_drift", _seed_schema_drift),
]


@pytest.mark.parametrize("kind,seeder", _KIND_MATRIX, ids=[k for k, _ in _KIND_MATRIX])
async def test_kind_fires_delivers_all_channels_content_free(
    async_session: AsyncSession,
    channels: tuple[dict[str, Notifier], _Recorder],
    kind: str,
    seeder: _Seeder,
) -> None:
    registry, recorder = channels
    engine = AlertEngine(registry)
    async_session.add(_rule(kind))
    await seeder(async_session)
    await async_session.commit()

    fired = await engine.run(async_session, now=_NOW)
    await async_session.commit()

    # Fired, recorded to alert_events, and every finding's context is content-free.
    assert len(fired) >= 1, f"{kind} did not fire"
    for item in fired:
        assert item.delivered is True
        assert_content_free(item.finding.context)
    events = int(
        (await async_session.execute(select(func.count()).select_from(models.AlertEvent)))
        .scalar_one()
    )
    assert events == len(fired)

    # All three real channels received every finding (2 HTTP calls + 1 SMTP each).
    assert len(recorder.http_urls) == 2 * len(fired)
    assert any("ntfy" in url or "tok" in url for url in recorder.http_urls)
    assert any("telegram" in url for url in recorder.http_urls)
    assert len(recorder.smtp_subjects) == len(fired)


# --------------------------------------------------------------------------- #
# Quiet hours and cooldown on a representative new kind (FR-ALERT-009)
# --------------------------------------------------------------------------- #


async def test_quiet_hours_suppresses_new_kind(
    async_session: AsyncSession, channels: tuple[dict[str, Notifier], _Recorder]
) -> None:
    registry, recorder = channels
    engine = AlertEngine(registry)
    # _NOW is 12:00; a window covering it suppresses the fire.
    async_session.add(_rule("failure_rate", quiet_hours={"start_hour": 11, "end_hour": 14}))
    await _seed_failure_rate(async_session)
    await async_session.commit()

    fired = await engine.run(async_session, now=_NOW)

    assert fired == []
    assert recorder.smtp_subjects == []


async def test_cooldown_suppresses_second_fire_new_kind(
    async_session: AsyncSession, channels: tuple[dict[str, Notifier], _Recorder]
) -> None:
    registry, _ = channels
    engine = AlertEngine(registry)
    async_session.add(_rule("failure_rate", cooldown_seconds=3600))
    await _seed_failure_rate(async_session)
    await async_session.commit()

    first = await engine.run(async_session, now=_NOW)
    await async_session.commit()
    second = await engine.run(async_session, now=_NOW + timedelta(minutes=5))
    await async_session.commit()

    assert len(first) == 1
    assert second == []  # inside cooldown
