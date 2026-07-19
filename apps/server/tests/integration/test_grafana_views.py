"""Grafana view fixtures: stable aggregation on the migrated schema (Task 66.7)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session
from tokemetry_server.db import models

_TS = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)


def _rollup(session: Session, **fields: Any) -> None:
    defaults: dict[str, Any] = {
        "day": date(2026, 7, 10), "provider": "anthropic", "model": "claude-sonnet-4-5",
        "machine": "", "project": "", "source": "", "environment": "",
        "billing_mode": "api_billed", "provenance": "derived",
        "input_tokens": 10, "output_tokens": 0, "cache_read_tokens": 0,
        "cache_write_short_tokens": 0, "cache_write_long_tokens": 0,
        "reasoning_tokens": 0, "total_tokens": 10, "unpriced_event_count": 0,
    }
    defaults.update(fields)
    session.add(models.DailyRollup(**defaults))


def test_grafana_daily_usage_view_collapses_v2_grain(migrated_engine: sa.Engine) -> None:
    with Session(migrated_engine) as session:
        # Two finer-grain rows differing only in billing_mode.
        _rollup(session, billing_mode="api_billed", input_tokens=10, total_tokens=10)
        _rollup(session, billing_mode="subscription", input_tokens=5, total_tokens=5)
        session.commit()

    with migrated_engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT provider, model, input_tokens, total_tokens "
                "FROM grafana_daily_usage_v2"
            )
        ).fetchall()
    assert len(rows) == 1  # collapsed back to the classic grain
    assert rows[0].input_tokens == 15 and rows[0].total_tokens == 15


def test_grafana_costs_view_splits_metrics(migrated_engine: sa.Engine) -> None:
    with Session(migrated_engine) as session:
        _rollup(session, billing_mode="api_billed", cost_priced_usd=Decimal("0.005"))
        _rollup(
            session, billing_mode="subscription",
            subscription_value_usd=Decimal("0.007"),
        )
        session.commit()

    with migrated_engine.connect() as conn:
        row = conn.execute(
            sa.text(
                "SELECT cost_priced_usd, subscription_value_usd FROM grafana_costs_v2"
            )
        ).one()
    assert Decimal(str(row.cost_priced_usd)) == Decimal("0.005")
    assert Decimal(str(row.subscription_value_usd)) == Decimal("0.007")


def test_grafana_limits_view_projects_snapshots(migrated_engine: sa.Engine) -> None:
    with Session(migrated_engine) as session:
        session.add(
            models.LimitSnapshot(
                provider="anthropic", machine="m1", ts=_TS, window_kind="five_hour",
                utilization_pct=Decimal("42.5"), provenance="official", raw={},
            )
        )
        session.commit()

    with migrated_engine.connect() as conn:
        row = conn.execute(
            sa.text(
                "SELECT provider, window_kind, utilization_pct, provenance "
                "FROM grafana_limits_v2"
            )
        ).one()
    assert row.provider == "anthropic" and row.window_kind == "five_hour"
    assert row.provenance == "official"
