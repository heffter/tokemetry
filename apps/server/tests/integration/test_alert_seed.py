"""Tests for default alert-rule seeding."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.db import models
from tokemetry_server.services.alerting.seed import seed_default_alert_rules


async def _rule_count(session: AsyncSession) -> int:
    return int(
        (await session.execute(select(func.count()).select_from(models.AlertRule))).scalar_one()
    )


async def test_seeds_defaults_when_empty(async_session: AsyncSession) -> None:
    added = await seed_default_alert_rules(async_session)
    await async_session.commit()
    assert added == 4
    assert await _rule_count(async_session) == 4


async def test_seeding_is_idempotent(async_session: AsyncSession) -> None:
    await seed_default_alert_rules(async_session)
    await async_session.commit()
    added_again = await seed_default_alert_rules(async_session)
    await async_session.commit()
    assert added_again == 0
    assert await _rule_count(async_session) == 4
