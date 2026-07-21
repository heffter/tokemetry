"""Rate-card administration and pricing reports (Task 64.10, FR-PRICE-022).

Admin operations over the v2 ``rate_cards`` grain: list (filterable), create
(with overlap rejection and priority/override precedence, FR-PRICE-004/005),
and close (set ``effective_to``). Every mutation is audited. Two operational
reports surface pricing gaps: unpriced/partial events (from ``computed_costs``)
and unknown-model observations (from ``data_quality_events``), so an operator
can see exactly which models still need a rate card (US-010).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.db import models
from tokemetry_server.services import audit
from tokemetry_server.services.pricing_v2 import check_overlap

#: Cost statuses that mean an event still lacks a full price.
_UNPRICED_STATUSES = ("unpriced", "partial")


@dataclass(frozen=True)
class UnpricedRow:
    """An aggregate of active events that are unpriced or partially priced."""

    provider: str
    native_model: str
    cost_status: str
    event_count: int


@dataclass(frozen=True)
class UnknownModelRow:
    """An unknown-model observation recorded at ingest."""

    provider: str
    native_model: str
    observations: int
    resolved: bool
    last_seen: datetime


async def list_rate_cards(
    session: AsyncSession,
    provider: str | None = None,
    native_model: str | None = None,
    unit_type: str | None = None,
    active_on: date | None = None,
) -> list[models.RateCard]:
    """List rate cards, optionally filtered by grain and active-on date."""
    card = models.RateCard
    stmt = select(card)
    if provider is not None:
        stmt = stmt.where(card.provider == provider)
    if native_model is not None:
        stmt = stmt.where(card.native_model == native_model)
    if unit_type is not None:
        stmt = stmt.where(card.unit_type == unit_type)
    if active_on is not None:
        stmt = stmt.where(
            card.effective_from <= active_on,
            or_(card.effective_to.is_(None), card.effective_to >= active_on),
        )
    stmt = stmt.order_by(
        card.provider, card.native_model, card.unit_type, card.effective_from
    )
    return list((await session.execute(stmt)).scalars())


def _audit(
    session: AsyncSession,
    actor: str | None,
    action: str,
    subject: str,
    detail: dict[str, object],
    now: datetime,
) -> None:
    """Write an audit_log entry for a pricing-admin mutation."""
    audit.record(
        session, actor=actor, action=action, subject=subject, detail=detail, ts=now
    )


async def create_rate_card(
    session: AsyncSession,
    actor: str | None,
    now: datetime,
    *,
    provider: str,
    native_model: str,
    unit_type: str,
    effective_from: date,
    unit_price: Decimal,
    currency: str = "USD",
    mode: str = "realtime",
    service_tier: str | None = None,
    context_bracket: str | None = None,
    region: str | None = None,
    source: str = "manual",
    priority: int = 0,
    override: bool = False,
    effective_to: date | None = None,
) -> models.RateCard:
    """Create a rate card after rejecting any same-grain date overlap.

    Raises:
        OverlapError: If a same-grain card's date range intersects (FR-PRICE-005).
    """
    await check_overlap(
        session, provider, native_model, unit_type, service_tier, mode,
        context_bracket, priority, effective_from, effective_to,
    )
    card = models.RateCard(
        provider=provider,
        native_model=native_model,
        unit_type=unit_type,
        effective_from=effective_from,
        effective_to=effective_to,
        currency=currency,
        region=region,
        service_tier=service_tier,
        mode=mode,
        context_bracket=context_bracket,
        unit_price=unit_price,
        source=source,
        verified_at=now,
        priority=priority,
        override=override,
        created_at=now,
    )
    session.add(card)
    await session.flush()
    _audit(
        session, actor, "rate_card_create", f"{provider}/{native_model}",
        {
            "unit_type": unit_type, "unit_price": str(unit_price),
            "priority": priority, "override": override, "source": source,
            "effective_from": effective_from.isoformat(),
        },
        now,
    )
    return card


async def close_rate_card(
    session: AsyncSession,
    actor: str | None,
    card_id: int,
    effective_to: date,
    now: datetime,
) -> models.RateCard | None:
    """Close a rate card by setting ``effective_to``; None if it does not exist."""
    card = await session.get(models.RateCard, card_id)
    if card is None:
        return None
    card.effective_to = effective_to
    _audit(
        session, actor, "rate_card_close", f"{card.provider}/{card.native_model}",
        {
            "rate_card_id": card_id, "unit_type": card.unit_type,
            "effective_to": effective_to.isoformat(),
        },
        now,
    )
    return card


async def unpriced_report(session: AsyncSession) -> list[UnpricedRow]:
    """Aggregate active events that are unpriced or partially priced, by model."""
    cost = models.ComputedCost
    event = models.UsageEventV2
    stmt = (
        select(
            event.provider,
            event.native_model,
            cost.cost_status,
            func.count().label("event_count"),
        )
        .select_from(cost)
        .join(
            event,
            (cost.provider == event.provider) & (cost.event_id == event.event_id),
        )
        .where(cost.active.is_(True), cost.cost_status.in_(_UNPRICED_STATUSES))
        .group_by(event.provider, event.native_model, cost.cost_status)
        .order_by(event.provider, event.native_model, cost.cost_status)
    )
    return [
        UnpricedRow(
            provider=row.provider,
            native_model=row.native_model,
            cost_status=row.cost_status,
            event_count=row.event_count,
        )
        for row in await session.execute(stmt)
    ]


async def unknown_models_report(session: AsyncSession) -> list[UnknownModelRow]:
    """List unknown-model observations recorded at ingest, newest first."""
    dq = models.DataQualityEvent
    stmt = (
        select(dq)
        .where(dq.kind == "unknown_model")
        .order_by(dq.ts.desc())
    )
    rows: list[UnknownModelRow] = []
    for event in (await session.execute(stmt)).scalars():
        detail = event.detail or {}
        provider = str(detail.get("provider", ""))
        native_model = str(detail.get("native_model", event.subject))
        observations = int(detail.get("count", 1)) if isinstance(detail, dict) else 1
        last_seen = (
            event.ts if event.ts.tzinfo is not None else event.ts.replace(tzinfo=UTC)
        )
        rows.append(
            UnknownModelRow(
                provider=provider,
                native_model=native_model,
                observations=observations,
                resolved=event.resolved,
                last_seen=last_seen,
            )
        )
    return rows
