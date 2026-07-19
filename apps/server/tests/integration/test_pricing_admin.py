"""Rate-card admin service: create/overlap/close, reports, precedence (64.10)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa
from conftest import make_v1_event
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.db import models
from tokemetry_server.services.computed_costs import record_cost
from tokemetry_server.services.pricing_admin import (
    close_rate_card,
    create_rate_card,
    list_rate_cards,
    unknown_models_report,
    unpriced_report,
)
from tokemetry_server.services.pricing_v2 import OverlapError, resolve_rate

_NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
_AT = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)


async def _create(session: AsyncSession, price: str, **overrides: object) -> models.RateCard:
    kwargs: dict[str, object] = {
        "provider": "anthropic",
        "native_model": "claude-sonnet-4-5",
        "unit_type": "input_token",
        "effective_from": date(2026, 1, 1),
        "unit_price": Decimal(price),
    }
    kwargs.update(overrides)
    return await create_rate_card(session, "admin", _NOW, **kwargs)  # type: ignore[arg-type]


async def test_create_persists_and_audits(async_session: AsyncSession) -> None:
    card = await _create(async_session, "0.000005")
    await async_session.commit()
    assert card.id is not None and card.source == "manual"

    audit = (
        await async_session.execute(
            sa.select(models.AuditLog).where(models.AuditLog.action == "rate_card_create")
        )
    ).scalar_one()
    assert audit.detail["unit_price"] == "0.000005"


async def test_create_rejects_overlapping_grain(async_session: AsyncSession) -> None:
    await _create(async_session, "0.000005")
    await async_session.commit()
    # Same grain, overlapping open date range -> rejected.
    with pytest.raises(OverlapError):
        await _create(async_session, "0.000006")


async def test_higher_priority_override_wins_in_resolution(
    async_session: AsyncSession,
) -> None:
    await _create(async_session, "0.000005", priority=0)
    await _create(async_session, "0.000010", priority=100, override=True)
    await async_session.commit()

    resolved = await resolve_rate(
        async_session, "anthropic", "claude-sonnet-4-5", "input_token", _AT
    )
    assert resolved is not None
    assert resolved.unit_price == Decimal("0.000010")  # override precedence (FR-PRICE-004)


async def test_close_sets_effective_to(async_session: AsyncSession) -> None:
    card = await _create(async_session, "0.000005")
    await async_session.commit()
    closed = await close_rate_card(async_session, "admin", card.id, date(2026, 6, 30), _NOW)
    await async_session.commit()
    assert closed is not None and closed.effective_to == date(2026, 6, 30)


async def test_close_unknown_card_returns_none(async_session: AsyncSession) -> None:
    assert await close_rate_card(async_session, "admin", 999, date(2026, 6, 30), _NOW) is None


async def test_list_filters_by_grain_and_active_on(async_session: AsyncSession) -> None:
    await _create(async_session, "0.000005", unit_type="input_token")
    await _create(async_session, "0.000015", unit_type="output_token")
    await _create(
        async_session, "0.000007", native_model="claude-haiku-4-5", unit_type="input_token"
    )
    await async_session.commit()

    sonnet = await list_rate_cards(async_session, native_model="claude-sonnet-4-5")
    assert {c.unit_type for c in sonnet} == {"input_token", "output_token"}
    inputs = await list_rate_cards(async_session, unit_type="input_token")
    assert len(inputs) == 2
    active = await list_rate_cards(async_session, active_on=date(2026, 7, 1))
    assert len(active) == 3  # all open and effective by then


async def test_unpriced_report_aggregates_by_model_and_status(
    async_session: AsyncSession,
) -> None:
    async_session.add(
        make_v1_event(provider="anthropic", event_id="a:1", model="mystery-1", ts=_AT)
    )
    async_session.add(
        make_v1_event(provider="anthropic", event_id="a:2", model="mystery-1", ts=_AT)
    )
    await async_session.flush()
    await record_cost(
        async_session, "anthropic", "a:1", amount=None,
        cost_status="unpriced", pricing_version="1",
    )
    await record_cost(
        async_session, "anthropic", "a:2", amount=None,
        cost_status="unpriced", pricing_version="1",
    )
    await async_session.commit()

    report = await unpriced_report(async_session)
    assert len(report) == 1
    assert report[0].native_model == "mystery-1"
    assert report[0].cost_status == "unpriced"
    assert report[0].event_count == 2


async def test_unknown_models_report_lists_observations(
    async_session: AsyncSession,
) -> None:
    async_session.add(
        models.DataQualityEvent(
            kind="unknown_model",
            subject="openai/o9",
            detail={"provider": "openai", "native_model": "o9"},
            ts=_AT,
            resolved=False,
        )
    )
    # A different kind must not appear in the report.
    async_session.add(
        models.DataQualityEvent(
            kind="clock_skew", subject="collector-1", detail={}, ts=_AT, resolved=False
        )
    )
    await async_session.commit()

    report = await unknown_models_report(async_session)
    assert len(report) == 1
    assert (report[0].provider, report[0].native_model) == ("openai", "o9")
    assert report[0].resolved is False
