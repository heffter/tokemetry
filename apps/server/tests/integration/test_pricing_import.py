"""Rate-card import: dry-run diff, audited apply, effective-date closure (64.9)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.pricing.sources.rate_card import RateCardRow
from tokemetry_server.db import models
from tokemetry_server.services.pricing_import import (
    DigestMismatchError,
    apply_import,
    compute_import_diff,
)

_NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
_IMPORT_DATE = date(2026, 7, 10)


def _row(unit_price: str, **overrides: Any) -> RateCardRow:
    defaults: dict[str, Any] = {
        "provider": "anthropic",
        "native_model": "claude-sonnet-4-5",
        "unit_type": "input_token",
        "effective_from": _IMPORT_DATE,
        "unit_price": Decimal(unit_price),
        "source": "litellm",
        "priority": 0,
    }
    defaults.update(overrides)
    return RateCardRow(**defaults)


async def _seed_card(session: AsyncSession, unit_price: str, **overrides: Any) -> int:
    defaults: dict[str, Any] = {
        "provider": "anthropic",
        "native_model": "claude-sonnet-4-5",
        "unit_type": "input_token",
        "effective_from": date(2026, 1, 1),
        "effective_to": None,
        "currency": "USD",
        "mode": "realtime",
        "unit_price": Decimal(unit_price),
        "source": "litellm",
        "priority": 0,
        "override": False,
        "created_at": _NOW,
    }
    defaults.update(overrides)
    card = models.RateCard(**defaults)
    session.add(card)
    await session.flush()
    return card.id


async def _cards(session: AsyncSession) -> list[models.RateCard]:
    result = await session.execute(
        sa.select(models.RateCard).order_by(models.RateCard.id)
    )
    return list(result.scalars())


async def test_diff_all_new_on_empty_db(async_session: AsyncSession) -> None:
    diff = await compute_import_diff(async_session, [_row("0.000005")], _IMPORT_DATE)
    assert (diff.new_count, diff.superseded_count, diff.unchanged_count, diff.conflict_count) == (
        1, 0, 0, 0,
    )
    assert len(diff.digest) == 64  # sha256 hex


async def test_apply_inserts_rows_and_writes_audit(async_session: AsyncSession) -> None:
    rows = [_row("0.000005")]
    diff = await compute_import_diff(async_session, rows, _IMPORT_DATE)
    result = await apply_import(
        async_session, rows, _IMPORT_DATE, diff.digest, "admin", "litellm+official", _NOW
    )
    await async_session.commit()

    assert result.applied_new == 1
    (card,) = await _cards(async_session)
    assert card.unit_price == Decimal("0.000005")
    assert card.effective_from == _IMPORT_DATE and card.effective_to is None

    audit = (
        await async_session.execute(
            sa.select(models.AuditLog).where(models.AuditLog.action == "pricing_import")
        )
    ).scalar_one()
    assert audit.detail["new"] == 1 and audit.detail["digest"] == diff.digest


async def test_reimport_same_price_is_unchanged(async_session: AsyncSession) -> None:
    rows = [_row("0.000005")]
    diff = await compute_import_diff(async_session, rows, _IMPORT_DATE)
    await apply_import(
        async_session, rows, _IMPORT_DATE, diff.digest, "admin", "src", _NOW
    )
    await async_session.commit()

    # A second import of the same price is a no-op diff (idempotent).
    again = await compute_import_diff(async_session, rows, _IMPORT_DATE)
    assert (again.new_count, again.superseded_count, again.unchanged_count) == (0, 0, 1)


async def test_superseded_closes_prior_and_opens_new(async_session: AsyncSession) -> None:
    await _seed_card(async_session, "0.000005", effective_from=date(2026, 1, 1))
    await async_session.commit()

    rows = [_row("0.000010")]  # a changed current price
    diff = await compute_import_diff(async_session, rows, _IMPORT_DATE)
    assert diff.superseded_count == 1
    await apply_import(async_session, rows, _IMPORT_DATE, diff.digest, "admin", "src", _NOW)
    await async_session.commit()

    cards = await _cards(async_session)
    old = next(c for c in cards if c.effective_from == date(2026, 1, 1))
    new = next(c for c in cards if c.effective_from == _IMPORT_DATE)
    assert old.effective_to == date(2026, 7, 9)  # closed the day before import
    assert new.unit_price == Decimal("0.000010") and new.effective_to is None


async def test_same_day_price_change_is_a_conflict(async_session: AsyncSession) -> None:
    # A stored card already effective on the import date is not silently rewritten.
    await _seed_card(async_session, "0.000005", effective_from=_IMPORT_DATE)
    await async_session.commit()

    rows = [_row("0.000010")]
    diff = await compute_import_diff(async_session, rows, _IMPORT_DATE)
    assert diff.conflict_count == 1
    result = await apply_import(
        async_session, rows, _IMPORT_DATE, diff.digest, "admin", "src", _NOW
    )
    await async_session.commit()

    assert result.conflicts == 1 and result.applied_new == 0
    (card,) = await _cards(async_session)
    assert card.unit_price == Decimal("0.000005")  # untouched


async def test_official_and_litellm_coexist_by_priority(async_session: AsyncSession) -> None:
    rows = [
        _row("0.000005", source="litellm", priority=0),
        _row("0.000006", source="official", priority=100),
    ]
    diff = await compute_import_diff(async_session, rows, _IMPORT_DATE)
    assert diff.new_count == 2  # different grain (priority) -> both new
    await apply_import(async_session, rows, _IMPORT_DATE, diff.digest, "admin", "src", _NOW)
    await async_session.commit()
    assert {c.source for c in await _cards(async_session)} == {"litellm", "official"}


async def test_apply_rejects_a_stale_digest(async_session: AsyncSession) -> None:
    rows = [_row("0.000005")]
    diff = await compute_import_diff(async_session, rows, _IMPORT_DATE)

    # The stored rates change after the dry run: the digest no longer matches.
    await _seed_card(async_session, "0.000005", effective_from=date(2026, 1, 1))
    await async_session.commit()

    with pytest.raises(DigestMismatchError):
        await apply_import(
            async_session, rows, _IMPORT_DATE, diff.digest, "admin", "src", _NOW
        )
