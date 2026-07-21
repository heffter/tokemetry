"""Restore verification (Task 70.6).

Runs on both engines via ``migration_url`` / ``migrated_engine`` (SQLite always,
Postgres when ``TOKEMETRY_TEST_POSTGRES_URL`` is set), so the verification that
gates a restore behaves identically on both.
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from sqlalchemy.orm import Session
from tokemetry_server.db import models
from tokemetry_server.db.migrate import upgrade_to_revision
from tokemetry_server.ops.restore_verify import verify_database

_PRE_HEAD_REVISION = "0026"


def _rollup(total_tokens: int) -> models.DailyRollup:
    """A daily rollup; tier sum is 150 so total=150 is consistent."""
    return models.DailyRollup(
        day=date(2026, 7, 10),
        provider="anthropic",
        model="claude-sonnet-4-5",
        input_tokens=100,
        output_tokens=50,
        total_tokens=total_tokens,
    )


def test_verify_passes_on_head_with_consistent_rollups(
    migration_url: str, migrated_engine: sa.Engine
) -> None:
    with Session(migrated_engine) as session:
        session.add(_rollup(150))
        session.commit()
    report = verify_database(migration_url)
    assert report.at_head
    assert report.missing_tables == []
    assert report.rollup_rows == 1
    assert report.rollup_inconsistencies == 0
    assert report.ok
    # A representative table count is reported.
    assert "daily_rollups" in report.table_counts


def test_verify_detects_inconsistent_rollup(
    migration_url: str, migrated_engine: sa.Engine
) -> None:
    """A tampered rollup (total != tier sum) fails verification."""
    with Session(migrated_engine) as session:
        session.add(_rollup(999))  # tier sum is 150
        session.commit()
    report = verify_database(migration_url)
    assert report.rollup_inconsistencies == 1
    assert not report.ok


def test_verify_fails_when_not_at_head(migration_url: str) -> None:
    """A backup restored to an older schema is rejected."""
    upgrade_to_revision(migration_url, _PRE_HEAD_REVISION)
    report = verify_database(migration_url)
    assert report.current_revision == _PRE_HEAD_REVISION
    assert not report.at_head
    assert not report.ok
