"""Asynchronous cost worker: price events out of the ingest path (FR-COST-009).

Ingest never computes cost, so ingest latency is independent of pricing
(NFR-PERF-005) and a cost failure never rejects usage (NFR-REL-007). This sweep
finds final attempt events in ``usage_events_v2`` that lack an active
``computed_costs`` row and prices them through :class:`CostEngineV2`, giving
eventual cost coverage after ingest bursts and after a worker restart. The
application runs it on a background loop (like the alert engine); it is also the
unit that a repricing job calls per event.
"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.db import models
from tokemetry_server.services.cost_v2 import CostEngineV2
from tokemetry_server.services.pricing_v2 import current_pricing_version


async def billable_units_for(
    session: AsyncSession, provider: str, event_id: str
) -> dict[str, float]:
    """Load an event's non-token billable units as a quantity map."""
    rows = await session.execute(
        select(models.BillableUnit).where(
            models.BillableUnit.provider == provider,
            models.BillableUnit.event_id == event_id,
        )
    )
    return {unit.unit_type: float(unit.quantity) for unit in rows.scalars()}


async def sweep_uncosted_costs(
    session: AsyncSession,
    batch_size: int = 500,
    billing_mode_overrides: Mapping[str, str] | None = None,
) -> int:
    """Price up to ``batch_size`` final attempts lacking an active cost; return count.

    ``billing_mode_overrides`` are the account-level machine -> billing_mode
    overrides passed through to the cost engine (D-007).
    """
    event = models.UsageEventV2
    cost = models.ComputedCost
    active_cost = (
        select(cost.id)
        .where(
            cost.provider == event.provider,
            cost.event_id == event.event_id,
            cost.active.is_(True),
        )
        .exists()
    )
    rows = (
        await session.execute(
            select(event)
            .where(
                event.event_kind == "attempt",
                event.finality == "final",
                ~active_cost,
            )
            .limit(batch_size)
        )
    ).scalars().all()
    if not rows:
        return 0

    engine = CostEngineV2(session, billing_mode_overrides=billing_mode_overrides)
    version = await current_pricing_version(session)
    for row in rows:
        billable = await billable_units_for(session, row.provider, row.event_id)
        await engine.compute_and_record_row(row, billable, version)
    return len(rows)
