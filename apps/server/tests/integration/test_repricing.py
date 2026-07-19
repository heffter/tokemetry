"""Auditable repricing and revert (FR-PRICE-019/020, FR-COST-002).

Repricing recomputes cost for a range under a new pricing version, retains the
prior rows, flips the active row atomically, and writes an audit entry. Revert
re-activates a named prior version, restoring the exact prior amounts. These
tests cover the amount/version/active transitions, prior-row retention, the
provider filter, the audit trail, and that an unpriceable event reprices to an
``unpriced`` row without raising (cost never propagates as an error, FR-COST-008).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from conftest import make_v1_event
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.db import models
from tokemetry_server.services.cost_worker import sweep_uncosted_costs
from tokemetry_server.services.repricing import reprice, revert

_TS = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)
_START = datetime(2026, 7, 1, tzinfo=UTC)
_END = datetime(2026, 8, 1, tzinfo=UTC)


async def _rate(
    session: AsyncSession, unit_type: str, price: str, **overrides: Any
) -> models.RateCard:
    """Add and return a rate card for anthropic/claude-sonnet-4-5."""
    defaults: dict[str, Any] = {
        "provider": "anthropic",
        "native_model": "claude-sonnet-4-5",
        "unit_type": unit_type,
        "effective_from": date(2026, 1, 1),
        "currency": "USD",
        "mode": "realtime",
        "unit_price": Decimal(price),
        "source": "default",
        "priority": 0,
        "override": False,
        "created_at": _TS,
    }
    defaults.update(overrides)
    card = models.RateCard(**defaults)
    session.add(card)
    return card


def _seed_event(
    session: AsyncSession, event_id: str, provider: str, model: str, **fields: Any
) -> None:
    """Add a final attempt ledger row."""
    session.add(
        make_v1_event(
            provider=provider, event_id=event_id, model=model, ts=_TS, **fields
        )
    )


async def _costs(session: AsyncSession, event_id: str) -> list[models.ComputedCost]:
    """Computed-cost rows for one event, ordered by pricing version."""
    result = await session.execute(
        sa.select(models.ComputedCost)
        .where(models.ComputedCost.event_id == event_id)
        .order_by(models.ComputedCost.pricing_version)
    )
    return list(result.scalars())


async def _active(session: AsyncSession, event_id: str) -> models.ComputedCost:
    """The single active cost row for one event."""
    result = await session.execute(
        sa.select(models.ComputedCost).where(
            models.ComputedCost.event_id == event_id,
            models.ComputedCost.active.is_(True),
        )
    )
    return result.scalar_one()


async def _audit(session: AsyncSession, action: str) -> models.AuditLog:
    """The single audit entry for an action."""
    result = await session.execute(
        sa.select(models.AuditLog).where(models.AuditLog.action == action)
    )
    return result.scalar_one()


async def test_reprice_recomputes_bumps_version_and_flips_active(
    async_session: AsyncSession,
) -> None:
    card = await _rate(async_session, "input_token", "0.000005")
    _seed_event(async_session, "anthropic:req_1", "anthropic", "claude-sonnet-4-5",
                input_tokens=1000)
    await async_session.commit()

    await sweep_uncosted_costs(async_session)
    await async_session.commit()

    # An operator edits the price, then reprices the affected range.
    card.unit_price = Decimal("0.000010")
    await async_session.commit()
    result = await reprice(async_session, "admin", _START, _END)
    await async_session.commit()

    assert result.pricing_version == "2"
    assert result.affected == 1

    active = await _active(async_session, "anthropic:req_1")
    assert active.pricing_version == "2"
    assert active.amount == Decimal("0.010000")  # 1000 * 0.00001

    # The prior version is retained but no longer active.
    versions = {c.pricing_version: c for c in await _costs(async_session, "anthropic:req_1")}
    assert versions["1"].active is False
    assert versions["1"].amount == Decimal("0.005000")  # 1000 * 0.000005


async def test_reprice_writes_an_audit_entry(async_session: AsyncSession) -> None:
    await _rate(async_session, "input_token", "0.000005")
    _seed_event(async_session, "anthropic:req_1", "anthropic", "claude-sonnet-4-5",
                input_tokens=1000)
    await async_session.commit()
    await sweep_uncosted_costs(async_session)
    await async_session.commit()

    await reprice(async_session, "admin", _START, _END)
    await async_session.commit()

    entry = await _audit(async_session, "reprice")
    assert entry.actor == "admin"
    assert entry.detail["affected"] == 1
    assert entry.detail["pricing_version"] == "2"


async def test_revert_restores_exact_prior_amounts(async_session: AsyncSession) -> None:
    card = await _rate(async_session, "input_token", "0.000005")
    _seed_event(async_session, "anthropic:req_1", "anthropic", "claude-sonnet-4-5",
                input_tokens=1000)
    await async_session.commit()
    await sweep_uncosted_costs(async_session)
    await async_session.commit()

    card.unit_price = Decimal("0.000010")
    await async_session.commit()
    await reprice(async_session, "admin", _START, _END)
    await async_session.commit()

    result = await revert(async_session, "admin", "1", _START, _END)
    await async_session.commit()

    assert result.affected == 1
    active = await _active(async_session, "anthropic:req_1")
    assert active.pricing_version == "1"
    assert active.amount == Decimal("0.005000")  # exact prior amount restored

    versions = {c.pricing_version: c for c in await _costs(async_session, "anthropic:req_1")}
    assert versions["2"].active is False  # the reprice row is retained, inactive

    entry = await _audit(async_session, "reprice_revert")
    assert entry.detail["reverted"] == 1
    assert entry.detail["pricing_version"] == "1"


async def test_reprice_honours_the_provider_filter(async_session: AsyncSession) -> None:
    await _rate(async_session, "input_token", "0.000005")
    await _rate(async_session, "input_token", "0.000007",
                provider="openai", native_model="gpt-5")
    _seed_event(async_session, "anthropic:req_1", "anthropic", "claude-sonnet-4-5",
                input_tokens=1000)
    _seed_event(async_session, "openai:req_1", "openai", "gpt-5", input_tokens=1000)
    await async_session.commit()
    await sweep_uncosted_costs(async_session)
    await async_session.commit()

    result = await reprice(async_session, "admin", _START, _END, provider="openai")
    await async_session.commit()

    assert result.affected == 1  # only the openai event
    assert (await _active(async_session, "openai:req_1")).pricing_version == "2"
    # The anthropic event is untouched: its v1 cost stays active.
    assert (await _active(async_session, "anthropic:req_1")).pricing_version == "1"


async def test_reprice_honours_the_native_model_filter(
    async_session: AsyncSession,
) -> None:
    await _rate(async_session, "input_token", "0.000005")
    await _rate(async_session, "input_token", "0.000006",
                native_model="claude-haiku-4-5")
    _seed_event(async_session, "anthropic:sonnet", "anthropic", "claude-sonnet-4-5",
                input_tokens=1000)
    _seed_event(async_session, "anthropic:haiku", "anthropic", "claude-haiku-4-5",
                input_tokens=1000)
    await async_session.commit()
    await sweep_uncosted_costs(async_session)
    await async_session.commit()

    result = await reprice(
        async_session, "admin", _START, _END, native_model="claude-haiku-4-5"
    )
    await async_session.commit()

    assert result.affected == 1  # only the haiku event
    assert (await _active(async_session, "anthropic:haiku")).pricing_version == "2"
    assert (await _active(async_session, "anthropic:sonnet")).pricing_version == "1"


async def test_revert_to_a_version_without_rows_is_a_noop(
    async_session: AsyncSession,
) -> None:
    await _rate(async_session, "input_token", "0.000005")
    _seed_event(async_session, "anthropic:req_1", "anthropic", "claude-sonnet-4-5",
                input_tokens=1000)
    await async_session.commit()
    await sweep_uncosted_costs(async_session)
    await async_session.commit()

    # No cost row was ever written under version "9"; revert affects nothing and
    # leaves the active cost untouched.
    result = await revert(async_session, "admin", "9", _START, _END)
    await async_session.commit()

    assert result.affected == 0
    assert (await _active(async_session, "anthropic:req_1")).pricing_version == "1"


async def test_reprice_of_unpriceable_event_does_not_raise(
    async_session: AsyncSession,
) -> None:
    # No rate cards exist for this model: repricing must record an unpriced row
    # rather than propagate an error out of the administrative job.
    _seed_event(async_session, "anthropic:req_1", "anthropic", "claude-sonnet-4-5",
                input_tokens=1000)
    await async_session.commit()

    result = await reprice(async_session, "admin", _START, _END)
    await async_session.commit()

    assert result.affected == 1
    active = await _active(async_session, "anthropic:req_1")
    assert active.cost_status == "unpriced"
    assert active.amount is None
