"""Integration tests for logical-request grouping and winning-attempt rules."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.usage_v2 import Routing, SourceRef, SourceType, UsageEventV2
from tokemetry_server.db import models
from tokemetry_server.services.ingest_v2 import IngestV2Service

_SOURCE = SourceRef(type=SourceType.GATEWAY, name="proxy", version="1")


def _event(event_id: str, minute: int = 0, **overrides: Any) -> UsageEventV2:
    ts = datetime(2026, 7, 10, 12, minute, 0, tzinfo=UTC)
    defaults: dict[str, Any] = {
        "schema_version": 2,
        "event_id": event_id,
        "event_kind": "attempt",
        "finality": "final",
        "sequence": 0,
        "provider": "anthropic",
        "native_model": "claude-sonnet-4-5",
        "requested_model": "relayplane:auto",
        "logical_request_id": "lr-1",
        "ts_started": ts,
        "ts_completed": ts,
        "output_tokens": 100,
        "source": _SOURCE,
    }
    defaults.update(overrides)
    return UsageEventV2.model_validate(defaults)


async def _ingest(session: AsyncSession, events: list[UsageEventV2]) -> None:
    await IngestV2Service(session).ingest(events)
    await session.commit()


async def _lr(session: AsyncSession) -> models.LogicalRequest | None:
    return await session.get(models.LogicalRequest, ("anthropic", "lr-1"))


async def test_fallback_chain(async_session: AsyncSession) -> None:
    await _ingest(
        async_session,
        [
            _event(
                "e1",
                minute=0,
                attempt_id="att-1",
                success=False,
                outcome="error",
                routing=Routing(policy="cascade", reason="complexity", attempt_index=0),
            ),
            _event(
                "e2",
                minute=1,
                attempt_id="att-2",
                success=True,
                outcome="success",
                routing=Routing(fallback_from="claude-sonnet-4-5", attempt_index=1),
            ),
        ],
    )
    record = await _lr(async_session)
    assert record is not None
    assert record.attempt_count == 2
    assert record.fallback_count == 1
    assert record.winning_attempt_id == "att-2"
    assert record.requested_model == "relayplane:auto"
    assert record.routing_policy == "cascade"  # from the first attempt
    assert record.routing_reason == "complexity"


async def test_out_of_order_arrival(async_session: AsyncSession) -> None:
    # The fallback (minute 1) is ingested before the original (minute 0).
    await _ingest(
        async_session,
        [_event("e2", minute=1, attempt_id="att-2", success=True, outcome="success")],
    )
    await _ingest(
        async_session,
        [
            _event(
                "e1",
                minute=0,
                attempt_id="att-1",
                success=False,
                routing=Routing(policy="cascade", reason="complexity"),
            )
        ],
    )
    record = await _lr(async_session)
    assert record is not None
    assert record.attempt_count == 2
    assert record.winning_attempt_id == "att-2"
    # Metadata comes from the earliest attempt regardless of arrival order.
    assert record.routing_policy == "cascade"
    assert record.ts_first is not None and record.ts_last is not None


async def test_last_success_wins(async_session: AsyncSession) -> None:
    await _ingest(
        async_session,
        [
            _event("e1", minute=0, attempt_id="att-1", success=True, outcome="success"),
            _event("e2", minute=2, attempt_id="att-2", success=True, outcome="success"),
        ],
    )
    record = await _lr(async_session)
    assert record is not None
    assert record.winning_attempt_id == "att-2"  # last completed


async def test_summary_event_adds_zero_usage(async_session: AsyncSession) -> None:
    await _ingest(
        async_session,
        [
            _event("e1", minute=0, attempt_id="att-1", success=True, outcome="success"),
            _event("e2", minute=1, attempt_id="att-2", success=False, output_tokens=100),
            _event(
                "summary",
                minute=2,
                event_kind="logical_request",
                output_tokens=999,
                requested_model="relayplane:auto",
            ),
        ],
    )
    record = await _lr(async_session)
    assert record is not None
    # The summary event does not count as an attempt.
    assert record.attempt_count == 2

    # Attempt token sums exclude the summary event (the v1 view is attempt-only).
    view_total = await async_session.scalar(
        sa.text("SELECT COALESCE(SUM(output_tokens), 0) FROM usage_events")
    )
    assert view_total == 200


async def test_failed_attempt_with_usage_is_counted(async_session: AsyncSession) -> None:
    await _ingest(
        async_session,
        [
            _event("e1", minute=0, attempt_id="att-1", success=False, output_tokens=50),
            _event("e2", minute=1, attempt_id="att-2", success=True, output_tokens=100),
        ],
    )
    record = await _lr(async_session)
    assert record is not None
    assert record.attempt_count == 2
    assert record.winning_attempt_id == "att-2"
    # The failed attempt's usage is still billable (present in the view).
    view_total = await async_session.scalar(
        sa.text("SELECT COALESCE(SUM(output_tokens), 0) FROM usage_events")
    )
    assert view_total == 150
