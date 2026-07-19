"""daily_rollups v2 grain: split dimensions, data migration, Grafana view (66.1)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from tokemetry_server.db import models
from tokemetry_server.db.migrate import upgrade_to_head, upgrade_to_revision


def _rollup(**overrides: Any) -> models.DailyRollup:
    defaults: dict[str, Any] = {
        "day": date(2026, 7, 1),
        "provider": "anthropic",
        "model": "claude-sonnet-4-5",
        "machine": "",
        "project": "",
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_short_tokens": 0,
        "cache_write_long_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }
    defaults.update(overrides)
    return models.DailyRollup(**defaults)


def test_new_grain_allows_a_billing_mode_split(migrated_engine: sa.Engine) -> None:
    # Two rows identical except billing_mode are distinct under the v2 grain.
    with Session(migrated_engine) as session:
        session.add(_rollup(billing_mode="api_billed", cost_priced_usd=Decimal("3")))
        session.add(
            _rollup(billing_mode="subscription", subscription_value_usd=Decimal("2"))
        )
        session.commit()
        assert session.query(models.DailyRollup).count() == 2


def test_duplicate_full_grain_is_rejected(migrated_engine: sa.Engine) -> None:
    with Session(migrated_engine) as session:
        session.add(_rollup())
        session.commit()
    with Session(migrated_engine) as session:
        session.add(_rollup())  # identical full grain
        with pytest.raises(IntegrityError):
            session.commit()


def test_data_migration_seeds_priced_cost_from_cost_usd(migration_url: str) -> None:
    upgrade_to_revision(migration_url, "0018")
    engine = sa.create_engine(migration_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO daily_rollups (day, provider, machine, model, project,"
                    " input_tokens, output_tokens, cache_read_tokens,"
                    " cache_write_short_tokens, cache_write_long_tokens, total_tokens,"
                    " cost_usd, provenance) VALUES ('2026-07-01','anthropic','','m','',"
                    "1,2,0,0,0,3,'0.0050000000','derived')"
                )
            )
    finally:
        engine.dispose()

    upgrade_to_head(migration_url)

    engine = sa.create_engine(migration_url)
    try:
        with engine.connect() as conn:
            priced = conn.execute(
                sa.text("SELECT cost_priced_usd FROM daily_rollups")
            ).scalar()
    finally:
        engine.dispose()
    assert Decimal(str(priced)) == Decimal("0.005")  # seeded from cost_usd


def test_grafana_view_aggregates_back_to_stable_grain(
    migrated_engine: sa.Engine,
) -> None:
    with Session(migrated_engine) as session:
        session.add(
            _rollup(
                billing_mode="api_billed", input_tokens=10, total_tokens=10,
                cost_usd=Decimal("3"), cost_priced_usd=Decimal("3"),
            )
        )
        session.add(
            _rollup(
                billing_mode="subscription", input_tokens=5, total_tokens=5,
                cost_usd=Decimal("2"), cost_priced_usd=Decimal("2"),
            )
        )
        session.commit()

    with migrated_engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT provider, model, input_tokens, cost_usd "
                "FROM daily_rollups_grafana"
            )
        ).fetchall()
    # The finer v2 grain collapses back to one stable (provider, model, ...) row.
    assert len(rows) == 1
    assert rows[0].input_tokens == 15
    assert Decimal(str(rows[0].cost_usd)) == Decimal("5")
