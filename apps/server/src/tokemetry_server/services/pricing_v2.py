"""v2 rate-card resolution and overlap validation (FR-PRICE-001/004/005).

Resolves the ``unit_price`` for one ``(provider, native_model, unit_type)`` at an
event's timestamp, applying the precedence lattice: a candidate must be
date-valid (``effective_from <= date <= effective_to``), match the unit type and
mode, and match the requested tier/context-bracket or fall back to a null one.
Among survivors the most specific tier and bracket win, then the highest
``priority``, then an ``override`` row at equal priority, then the most recent
``effective_from`` (FR-PRICE-004 precedence). The dated-to-base model fallback is
preserved from the v1 pricing table so dated Claude ids keep resolving.

Historical cost always uses the rate effective at event time (FR-PRICE-001), so
adding rows with a later ``effective_from`` never changes past results
(FR-PRICE-016). Overlap validation on write rejects two rows with an identical
grain and intersecting date ranges (FR-PRICE-005). All arithmetic is ``Decimal``
(FR-PRICE-017).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.pricing.table import base_model_id

from tokemetry_server.db import models

#: app_settings key holding the monotonic pricing-state version. Bumped on every
#: rate-card apply so a recompute under an unchanged pricing state is idempotent
#: and a change forces re-pricing (FR-PRICE-018).
PRICING_VERSION_KEY = "pricing_version"


async def current_pricing_version(session: AsyncSession) -> str:
    """The current pricing-state version (default ``'1'`` before any change)."""
    row = await session.get(models.AppSetting, PRICING_VERSION_KEY)
    return row.value if row is not None and row.value else "1"


async def bump_pricing_version(session: AsyncSession) -> str:
    """Increment the pricing-state version and return the new value."""
    now = datetime.now(UTC)
    row = await session.get(models.AppSetting, PRICING_VERSION_KEY)
    if row is None:
        session.add(models.AppSetting(key=PRICING_VERSION_KEY, value="2", updated_at=now))
        return "2"
    current = int(row.value) if row.value.isdigit() else 1
    row.value = str(current + 1)
    row.updated_at = now
    return row.value


@dataclass(frozen=True)
class ResolvedRate:
    """The rate that applied to one unit at one time."""

    unit_price: Decimal
    rate_card_id: int
    currency: str
    source: str


class OverlapError(ValueError):
    """A rate card overlaps an existing one on the same grain (FR-PRICE-005)."""


def _as_date(at: date | datetime) -> date:
    """The date component of an event timestamp."""
    return at.date() if isinstance(at, datetime) else at


async def _candidate_models(
    session: AsyncSession, provider: str, native_model: str
) -> AsyncIterator[str]:
    """Yield model ids to try, mirroring the v1 dated/undated fallback."""
    yield native_model
    base = base_model_id(native_model)
    if base != native_model:
        yield base
        return
    # Undated query: try dated snapshots sharing the base, newest first.
    rows = (
        await session.execute(
            select(models.RateCard.native_model)
            .where(models.RateCard.provider == provider)
            .distinct()
        )
    ).scalars()
    dated = sorted(
        (
            model
            for model in rows
            if model != native_model and base_model_id(model) == native_model
        ),
        reverse=True,
    )
    for model in dated:
        yield model


async def resolve_rate(
    session: AsyncSession,
    provider: str,
    native_model: str,
    unit_type: str,
    at: date | datetime,
    tier: str | None = None,
    mode: str = "realtime",
    context_bracket: str | None = None,
) -> ResolvedRate | None:
    """Return the applicable rate, or ``None`` when no card matches."""
    on = _as_date(at)
    async for model in _candidate_models(session, provider, native_model):
        card = await _best_card(
            session, provider, model, unit_type, on, tier, mode, context_bracket
        )
        if card is not None:
            return ResolvedRate(
                unit_price=card.unit_price,
                rate_card_id=card.id,
                currency=card.currency,
                source=card.source,
            )
    return None


async def _best_card(
    session: AsyncSession,
    provider: str,
    model: str,
    unit_type: str,
    on: date,
    tier: str | None,
    mode: str,
    bracket: str | None,
) -> models.RateCard | None:
    """The single best rate card for one model, or None."""
    card = models.RateCard
    stmt = select(card).where(
        card.provider == provider,
        card.native_model == model,
        card.unit_type == unit_type,
        card.mode == mode,
        card.effective_from <= on,
        or_(card.effective_to.is_(None), card.effective_to >= on),
    )
    stmt = (
        stmt.where(or_(card.service_tier == tier, card.service_tier.is_(None)))
        if tier is not None
        else stmt.where(card.service_tier.is_(None))
    )
    stmt = (
        stmt.where(or_(card.context_bracket == bracket, card.context_bracket.is_(None)))
        if bracket is not None
        else stmt.where(card.context_bracket.is_(None))
    )

    rows = (await session.execute(stmt)).scalars().all()
    if not rows:
        return None
    return max(rows, key=lambda row: _precedence(row, tier, bracket))


def _precedence(
    row: models.RateCard, tier: str | None, bracket: str | None
) -> tuple[int, int, int, int, date]:
    """Sort key implementing the FR-PRICE-004 precedence (higher wins)."""
    return (
        1 if tier is not None and row.service_tier == tier else 0,
        1 if bracket is not None and row.context_bracket == bracket else 0,
        row.priority,
        1 if row.override else 0,
        row.effective_from,
    )


def _null_match(column: Any, value: str | None) -> ColumnElement[bool]:
    """An equality that also matches when both sides are NULL."""
    match: ColumnElement[bool] = column == value if value is not None else column.is_(None)
    return match


def _ranges_intersect(
    a_from: date, a_to: date | None, b_from: date, b_to: date | None
) -> bool:
    """Whether two effective-date ranges intersect (open-ended = ``date.max``)."""
    a_end = a_to if a_to is not None else date.max
    b_end = b_to if b_to is not None else date.max
    return a_from <= b_end and b_from <= a_end


async def check_overlap(
    session: AsyncSession,
    provider: str,
    native_model: str,
    unit_type: str,
    tier: str | None,
    mode: str,
    context_bracket: str | None,
    priority: int,
    effective_from: date,
    effective_to: date | None,
    exclude_id: int | None = None,
) -> None:
    """Raise :class:`OverlapError` if a same-grain card's date range intersects.

    The grain is ``(provider, native_model, unit_type, tier, mode, bracket,
    priority)``; two cards on that grain may not cover intersecting dates
    (FR-PRICE-005).
    """
    card = models.RateCard
    stmt = select(card).where(
        card.provider == provider,
        card.native_model == native_model,
        card.unit_type == unit_type,
        card.mode == mode,
        card.priority == priority,
        _null_match(card.service_tier, tier),
        _null_match(card.context_bracket, context_bracket),
    )
    if exclude_id is not None:
        stmt = stmt.where(card.id != exclude_id)
    for existing in (await session.execute(stmt)).scalars():
        if _ranges_intersect(
            effective_from, effective_to, existing.effective_from, existing.effective_to
        ):
            raise OverlapError(
                f"rate card for {provider}/{native_model} {unit_type} overlaps "
                f"card {existing.id} on the same grain"
            )
