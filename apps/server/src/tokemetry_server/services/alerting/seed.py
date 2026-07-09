"""Seed a sensible default alert-rule set on first run.

An empty rules table means a fresh install alerts on nothing. Seeding encodes
best-practice defaults (dual warn/critical limit thresholds, exhaustion
prediction, collector liveness) and doubles as configuration examples.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.db import models


def _default_rules() -> list[models.AlertRule]:
    """Build (but do not persist) the default rule set."""
    return [
        models.AlertRule(
            name="5-hour block",
            kind="limit_pct",
            window_kind="five_hour",
            warn_threshold=Decimal("80"),
            crit_threshold=Decimal("95"),
            channels=["ntfy"],
        ),
        models.AlertRule(
            name="Weekly limit",
            kind="limit_pct",
            window_kind="seven_day",
            warn_threshold=Decimal("80"),
            crit_threshold=Decimal("95"),
            channels=["ntfy"],
        ),
        models.AlertRule(
            name="Predicted exhaustion",
            kind="predicted_exhaustion",
            channels=["ntfy"],
        ),
        models.AlertRule(
            name="Collector offline",
            kind="collector_stale",
            warn_threshold=Decimal("30"),
            crit_threshold=Decimal("120"),
            channels=["ntfy"],
        ),
    ]


async def seed_default_alert_rules(session: AsyncSession) -> int:
    """Insert the default rules only when the table is empty; return count added."""
    existing = (
        await session.execute(select(func.count()).select_from(models.AlertRule))
    ).scalar_one()
    if existing:
        return 0
    rules = _default_rules()
    session.add_all(rules)
    return len(rules)
