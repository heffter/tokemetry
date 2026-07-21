"""Provider-neutral v2 heatmap (Task 74, Gap 1)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.db import models
from tokemetry_server.services.heatmap_v2 import build_heatmap
from tokemetry_server.services.query_framework import QueryFilters

_D1 = datetime(2026, 7, 6, 9, 0, tzinfo=UTC)  # a Monday, 09:00 UTC
_D2 = datetime(2026, 7, 7, 14, 0, tzinfo=UTC)  # Tuesday, 14:00 UTC


def _event(
    event_id: str, provider: str, ts: datetime, output: int
) -> models.UsageEventV2:
    return models.UsageEventV2(
        provider=provider,
        event_id=event_id,
        schema_version=2,
        event_kind="attempt",
        finality="final",
        sequence=0,
        native_model="claude-sonnet-4-5",
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


async def _seed(session: AsyncSession) -> None:
    session.add(_event("a1", "anthropic", _D1, 100))
    session.add(_event("a2", "anthropic", _D1, 50))  # same cell as a1
    session.add(_event("a3", "anthropic", _D2, 30))
    session.add(_event("o1", "openai", _D1, 7))
    await session.commit()


async def test_punch_card_calendar_and_total(async_session: AsyncSession) -> None:
    await _seed(async_session)
    heatmap = await build_heatmap(
        async_session, QueryFilters(), date(2026, 7, 1), date(2026, 7, 31)
    )
    punch = {(c.weekday, c.hour): c.value for c in heatmap.punch_card}
    # Monday 09:00 accumulates anthropic (150) + openai (7).
    assert punch[(_D1.weekday(), 9)] == 157
    assert punch[(_D2.weekday(), 14)] == 30
    calendar = {c.day: c.value for c in heatmap.calendar}
    assert calendar[date(2026, 7, 6)] == 157
    assert calendar[date(2026, 7, 7)] == 30
    assert heatmap.total_tokens == 187


async def test_filter_scopes_heatmap(async_session: AsyncSession) -> None:
    await _seed(async_session)
    heatmap = await build_heatmap(
        async_session,
        QueryFilters(provider="openai"),
        date(2026, 7, 1),
        date(2026, 7, 31),
    )
    assert heatmap.total_tokens == 7
    punch = {(c.weekday, c.hour): c.value for c in heatmap.punch_card}
    assert punch == {(_D1.weekday(), 9): 7}


async def test_range_excludes_out_of_window(async_session: AsyncSession) -> None:
    await _seed(async_session)
    # A one-day window on 2026-07-07 excludes the 07-06 events.
    heatmap = await build_heatmap(
        async_session, QueryFilters(), date(2026, 7, 7), date(2026, 7, 7)
    )
    assert heatmap.total_tokens == 30


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #

def _wire_event(event_id: str, provider: str, ts: str, output: int) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "event_id": event_id,
        "event_kind": "attempt",
        "finality": "final",
        "sequence": 1,
        "provider": provider,
        "native_model": "claude-sonnet-4-5",
        "ts_started": ts,
        "output_tokens": output,
        "source": {"type": "gateway", "name": "proxy", "version": "1"},
    }


def test_heatmap_endpoint(client: TestClient, auth: dict[str, str]) -> None:
    response = client.post(
        "/api/v2/ingest/events",
        json={
            "schema_version": 2,
            "events": [
                _wire_event("a1", "anthropic", "2026-07-06T09:00:00Z", 100),
                _wire_event("o1", "openai", "2026-07-06T09:00:00Z", 7),
            ],
        },
        headers=auth,
    )
    assert response.status_code == 200, response.text

    body = client.get(
        "/api/v2/heatmap",
        params={"from": "2026-07-01", "to": "2026-07-31"},
        headers=auth,
    ).json()
    assert body["metadata"]["total_tokens"] == 107
    assert body["metadata"]["date_from"] == "2026-07-01"
    cell = next(c for c in body["punch_card"] if c["hour"] == 9)
    assert cell["value"] == 107
    assert any(c["date"] == "2026-07-06" for c in body["calendar"])


def test_heatmap_endpoint_applies_and_reports_filter(
    client: TestClient, auth: dict[str, str]
) -> None:
    client.post(
        "/api/v2/ingest/events",
        json={
            "schema_version": 2,
            "events": [
                _wire_event("a1", "anthropic", "2026-07-06T09:00:00Z", 100),
                _wire_event("o1", "openai", "2026-07-06T09:00:00Z", 7),
            ],
        },
        headers=auth,
    )
    body = client.get(
        "/api/v2/heatmap",
        params={"from": "2026-07-01", "to": "2026-07-31", "provider": "openai"},
        headers=auth,
    ).json()
    assert body["metadata"]["total_tokens"] == 7
    assert body["metadata"]["applied_filters"] == {"provider": "openai"}


def test_heatmap_requires_auth(client: TestClient) -> None:
    assert client.get("/api/v2/heatmap").status_code == 401
