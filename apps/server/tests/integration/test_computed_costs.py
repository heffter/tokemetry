"""Computed-cost materialization and active-row uniqueness."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from tokemetry_server.db import models
from tokemetry_server.db.pricing_migration import materialize_computed_costs
from tokemetry_server.services.computed_costs import active_cost, record_cost

_TS = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)


def _ledger_row(session: Session, event_id: str, cost: Decimal | None) -> None:
    session.add(
        models.UsageEventV2(
            provider="anthropic",
            event_id=event_id,
            schema_version=2,
            event_kind="attempt",
            finality="final",
            sequence=0,
            native_model="claude-sonnet-4-5",
            ts_started=_TS,
            success=True,
            provenance="local_estimate",
            cost_usd=cost,
            dimensions={},
            extra={},
        )
    )


def test_materialization_reproduces_v1_cost(migrated_engine: sa.Engine) -> None:
    with Session(migrated_engine) as session:
        _ledger_row(session, "priced-1", Decimal("1.2345"))
        _ledger_row(session, "unpriced-1", None)
        session.commit()

    with migrated_engine.begin() as connection:
        materialized = materialize_computed_costs(connection)
    assert materialized == 2

    with Session(migrated_engine) as session:
        costs = {
            c.event_id: c
            for c in session.execute(sa.select(models.ComputedCost)).scalars()
        }
    assert costs["priced-1"].amount == Decimal("1.2345")
    assert costs["priced-1"].cost_status == "priced"
    assert costs["priced-1"].pricing_version == "v1-legacy"
    assert costs["priced-1"].active is True
    assert costs["unpriced-1"].amount is None
    assert costs["unpriced-1"].cost_status == "unpriced"


async def _seed_event(session: AsyncSession) -> None:
    session.add(
        models.UsageEventV2(
            provider="anthropic",
            event_id="req-1",
            schema_version=2,
            event_kind="attempt",
            finality="final",
            sequence=0,
            native_model="claude-sonnet-4-5",
            ts_started=_TS,
            success=True,
            provenance="local_estimate",
            dimensions={},
            extra={},
        )
    )
    await session.flush()


async def test_record_cost_keeps_one_active_row(async_session: AsyncSession) -> None:
    await _seed_event(async_session)
    await record_cost(
        async_session, "anthropic", "req-1",
        amount=Decimal("5"), cost_status="priced", pricing_version="v1",
    )
    await async_session.commit()

    # Reprice under a new version: the old row deactivates, the new is active.
    await record_cost(
        async_session, "anthropic", "req-1",
        amount=Decimal("6"), cost_status="priced", pricing_version="v2",
    )
    await async_session.commit()

    rows = (
        await async_session.execute(sa.select(models.ComputedCost))
    ).scalars().all()
    assert len(rows) == 2  # both versions retained for audit
    active = [r for r in rows if r.active]
    assert len(active) == 1
    assert active[0].pricing_version == "v2"
    assert active[0].amount == Decimal("6")

    current = await active_cost(async_session, "anthropic", "req-1")
    assert current is not None and current.pricing_version == "v2"


async def test_record_cost_upserts_same_version(async_session: AsyncSession) -> None:
    await _seed_event(async_session)
    await record_cost(
        async_session, "anthropic", "req-1",
        amount=Decimal("5"), cost_status="priced", pricing_version="v1",
    )
    await record_cost(
        async_session, "anthropic", "req-1",
        amount=Decimal("7"), cost_status="priced", pricing_version="v1",
    )
    await async_session.commit()
    rows = (
        await async_session.execute(sa.select(models.ComputedCost))
    ).scalars().all()
    assert len(rows) == 1  # same version upserts
    assert rows[0].amount == Decimal("7")


async def test_record_cost_rejects_unknown_status(async_session: AsyncSession) -> None:
    await _seed_event(async_session)
    with pytest.raises(ValueError, match="unknown cost_status"):
        await record_cost(
            async_session, "anthropic", "req-1",
            amount=None, cost_status="freebie", pricing_version="v1",
        )
