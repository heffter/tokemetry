"""Auditable repricing and revert (FR-PRICE-019/020, FR-COST-002).

Repricing is an explicit administrative operation: it bumps the pricing-state
version, recomputes cost for the events in a time range (optionally filtered by
provider/model) as new ``computed_costs`` rows under the new version, flips the
active row per event atomically, and retains the prior rows so the operation is
reversible. Every reprice and revert writes an ``audit_log`` entry (actor,
filters, affected count, pricing versions). Revert re-activates a named prior
pricing version for the same range, restoring the exact prior amounts. Rollup
recomputation for the affected days is a Task 66 dependency (noted, not wired).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.db import models
from tokemetry_server.services.cost_v2 import CostEngineV2
from tokemetry_server.services.cost_worker import billable_units_for
from tokemetry_server.services.pricing_v2 import bump_pricing_version
from tokemetry_server.services.rollups import refresh_rollups_for_days


@dataclass(frozen=True)
class RepriceResult:
    """The outcome of a reprice or revert run."""

    pricing_version: str
    affected: int


def _matching_events(
    start: datetime,
    end: datetime,
    provider: str | None,
    native_model: str | None,
) -> Select[tuple[models.UsageEventV2]]:
    """Select final attempt events in a range, optionally filtered."""
    event = models.UsageEventV2
    stmt = select(event).where(
        event.event_kind == "attempt",
        event.finality == "final",
        event.ts_started >= start,
        event.ts_started <= end,
    )
    if provider is not None:
        stmt = stmt.where(event.provider == provider)
    if native_model is not None:
        stmt = stmt.where(event.native_model == native_model)
    return stmt


async def reprice(
    session: AsyncSession,
    actor: str | None,
    start: datetime,
    end: datetime,
    provider: str | None = None,
    native_model: str | None = None,
    billing_mode_overrides: Mapping[str, str] | None = None,
) -> RepriceResult:
    """Recompute cost for a range under a new pricing version; audited."""
    version = await bump_pricing_version(session)
    engine = CostEngineV2(session, billing_mode_overrides=billing_mode_overrides)
    rows = (
        await session.execute(_matching_events(start, end, provider, native_model))
    ).scalars().all()
    for row in rows:
        billable = await billable_units_for(session, row.provider, row.event_id)
        await engine.compute_and_record_row(row, billable, version)

    await _refresh_affected_rollups(session, rows)
    _audit(
        session, actor, "reprice", provider, native_model,
        {"start": start.isoformat(), "end": end.isoformat(), "affected": len(rows),
         "pricing_version": version},
    )
    return RepriceResult(pricing_version=version, affected=len(rows))


async def _refresh_affected_rollups(
    session: AsyncSession, rows: Sequence[models.UsageEventV2]
) -> None:
    """Recompute rollups for the days a reprice touched (FR-ROLLUP-009)."""
    bind = session.bind
    if not rows or bind is None:
        return
    days = {
        (r.ts_started if r.ts_started.tzinfo else r.ts_started.replace(tzinfo=UTC)).date()
        for r in rows
    }
    await refresh_rollups_for_days(session, bind.dialect.name, sorted(days))


async def revert(
    session: AsyncSession,
    actor: str | None,
    pricing_version: str,
    start: datetime,
    end: datetime,
    provider: str | None = None,
    native_model: str | None = None,
) -> RepriceResult:
    """Re-activate a named prior pricing version for a range; audited."""
    cost = models.ComputedCost
    rows = (
        await session.execute(_matching_events(start, end, provider, native_model))
    ).scalars().all()
    reverted = 0
    for row in rows:
        target = (
            await session.execute(
                select(cost).where(
                    cost.provider == row.provider,
                    cost.event_id == row.event_id,
                    cost.pricing_version == pricing_version,
                )
            )
        ).scalar_one_or_none()
        if target is None:
            continue
        await session.execute(
            update(cost)
            .where(
                cost.provider == row.provider,
                cost.event_id == row.event_id,
                cost.active.is_(True),
            )
            .values(active=False)
        )
        target.active = True
        reverted += 1

    _audit(
        session, actor, "reprice_revert", provider, native_model,
        {"start": start.isoformat(), "end": end.isoformat(), "reverted": reverted,
         "pricing_version": pricing_version},
    )
    return RepriceResult(pricing_version=pricing_version, affected=reverted)


def _audit(
    session: AsyncSession,
    actor: str | None,
    action: str,
    provider: str | None,
    native_model: str | None,
    detail: dict[str, object],
) -> None:
    """Write an audit_log entry for a pricing operation."""
    session.add(
        models.AuditLog(
            actor=actor,
            action=action,
            subject=f"{provider or '*'}/{native_model or '*'}",
            detail=detail,
            ts=datetime.now(UTC),
        )
    )
