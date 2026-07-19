"""Billable-units storage, ingest round-trip, and atomic replacement."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from tokemetry_core.usage_v2 import SourceRef, SourceType, UsageEventV2
from tokemetry_server.db import models
from tokemetry_server.services.ingest_v2 import IngestV2Service

_TS = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)
_SOURCE = SourceRef(type=SourceType.GATEWAY, name="proxy", version="1")


def _event(event_id: str = "anthropic:req_1", **overrides: Any) -> UsageEventV2:
    defaults: dict[str, Any] = {
        "schema_version": 2,
        "event_id": event_id,
        "event_kind": "attempt",
        "finality": "final",
        "sequence": 1,
        "provider": "anthropic",
        "native_model": "claude-sonnet-4-5",
        "ts_started": _TS,
        "output_tokens": 100,
        "source": _SOURCE,
    }
    defaults.update(overrides)
    return UsageEventV2.model_validate(defaults)


async def _units(session: AsyncSession) -> list[models.BillableUnit]:
    rows = await session.execute(sa.select(models.BillableUnit))
    return list(rows.scalars().all())


def test_billable_unit_grain_is_unique(migrated_engine: sa.Engine) -> None:
    """One row per (provider, event_id, unit_type); the FK is enforced on Postgres."""
    with Session(migrated_engine) as session:
        session.add(
            models.BillableUnit(
                provider="anthropic",
                event_id="e1",
                unit_type="web_search_request",
                quantity=Decimal("2"),
            )
        )
        session.commit()
    with Session(migrated_engine) as session:
        session.add(
            models.BillableUnit(
                provider="anthropic",
                event_id="e1",
                unit_type="web_search_request",
                quantity=Decimal("5"),
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


async def test_ingest_stores_billable_units(async_session: AsyncSession) -> None:
    await IngestV2Service(async_session).ingest(
        [_event(billable_units={"web_search_request": 2, "tool_call": 3})]
    )
    await async_session.commit()
    units = {u.unit_type: u.quantity for u in await _units(async_session)}
    assert units == {
        "web_search_request": Decimal("2"),
        "tool_call": Decimal("3"),
    }


async def test_supersede_replaces_units_atomically(async_session: AsyncSession) -> None:
    service = IngestV2Service(async_session)
    await service.ingest(
        [_event(finality="snapshot", sequence=1, billable_units={"web_search_request": 2})]
    )
    await async_session.commit()
    await service.ingest(
        [_event(finality="final", sequence=2, billable_units={"web_search_request": 5})]
    )
    await async_session.commit()

    units = await _units(async_session)
    assert len(units) == 1  # replaced, not accumulated
    assert units[0].quantity == Decimal("5")


async def test_supersede_clears_units_when_new_has_none(
    async_session: AsyncSession,
) -> None:
    service = IngestV2Service(async_session)
    await service.ingest(
        [_event(finality="snapshot", sequence=1, billable_units={"tool_call": 4})]
    )
    await async_session.commit()
    await service.ingest([_event(finality="final", sequence=2)])  # no units
    await async_session.commit()
    assert await _units(async_session) == []
