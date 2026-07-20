"""Migration/backfill tests: v1-to-v2 copy, verification, resume, downgrade."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session
from tokemetry_server.db import models
from tokemetry_server.db.backfill import (
    BACKFILL_MARKER,
    V1_NAMESPACE,
    backfill_usage_events_v2,
    remove_backfilled_rows,
    verify_backfill,
)

_TS = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)


def _add_v1(session: Session, event_id: str, **overrides: object) -> None:
    """Add a representative v1 usage_events row."""
    defaults: dict[str, object] = {
        "provider": "anthropic",
        "event_id": event_id,
        "machine": "devbox-01",
        "session_id": "sess-1",
        "ts": _TS,
        "model": "claude-sonnet-4-5",
        "project": "proj",
        "git_branch": "main",
        "client_version": "1.0",
        "entrypoint": "cli",
        "is_sidechain": False,
        "session_kind": "interactive",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 10,
        "cache_write_short_tokens": 5,
        "cache_write_long_tokens": 2,
        "service_tier": "standard",
        "speed": "fast",
        "cost_usd": Decimal("1.2345"),
        "provenance": "official",
        "source": "collector",
        "extra": {"web_search": 1},
    }
    defaults.update(overrides)
    session.add(models.UsageEvent(**defaults))


def _seed_representative(session: Session) -> None:
    """Seed rows spanning days, machines, null project, and null-cost unknowns."""
    _add_v1(session, "a1")
    _add_v1(session, "a2", ts=datetime(2026, 7, 11, 9, 0, 0, tzinfo=UTC), output_tokens=70)
    _add_v1(session, "a3", machine="laptop-02", project=None)
    _add_v1(
        session,
        "u1",
        provider="openai",
        model="gpt-mystery",
        cost_usd=None,  # unknown model, unpriced
        output_tokens=0,
    )


def _backfill(engine: sa.Engine, chunk_size: int = 10_000) -> int:
    with engine.begin() as connection:
        return backfill_usage_events_v2(connection, chunk_size=chunk_size)


def test_backfill_maps_all_columns(pre_view_engine: sa.Engine) -> None:
    with Session(pre_view_engine) as session:
        _add_v1(session, "a1")
        session.commit()

    _backfill(pre_view_engine)

    # Read explicit typed columns rather than the full ORM entity: this DB is
    # migrated to a pre-view revision that predates later columns (observed_cost),
    # so a full-entity load would reference a not-yet-existing column.
    table = models.UsageEventV2.__table__
    with pre_view_engine.connect() as connection:
        row = (
            connection.execute(
                sa.select(
                    table.c.event_kind, table.c.finality, table.c.sequence,
                    table.c.native_model, table.c.requested_model,
                    table.c.reasoning_tokens, table.c.input_tokens, table.c.success,
                    table.c.source_id, table.c.extra,
                ).where(table.c.provider == "anthropic", table.c.event_id == "a1")
            )
            .mappings()
            .one_or_none()
        )
    assert row is not None
    assert row["event_kind"] == "attempt"
    assert row["finality"] == "final"
    assert row["sequence"] == 0
    assert row["native_model"] == "claude-sonnet-4-5"
    assert row["requested_model"] is None
    assert row["reasoning_tokens"] == 0
    assert row["input_tokens"] == 100
    assert row["success"] is True
    assert row["source_id"] is None
    assert row["extra"]["web_search"] == 1
    assert row["extra"][BACKFILL_MARKER] is True
    assert row["extra"][V1_NAMESPACE]["git_branch"] == "main"
    assert row["extra"][V1_NAMESPACE]["cost_usd"] == "1.2345000000"


def test_verify_reports_all_equal(pre_view_engine: sa.Engine) -> None:
    with Session(pre_view_engine) as session:
        _seed_representative(session)
        session.commit()

    _backfill(pre_view_engine)

    with pre_view_engine.connect() as connection:
        report = verify_backfill(connection)
    assert report.ok
    assert report.groups_checked >= 3
    assert report.mismatches == ()


def test_backfill_is_idempotent_and_resumable(pre_view_engine: sa.Engine) -> None:
    with Session(pre_view_engine) as session:
        _seed_representative(session)
        session.commit()

    # chunk_size=1 exercises the keyset pagination; a second run must not dup.
    _backfill(pre_view_engine, chunk_size=1)
    _backfill(pre_view_engine, chunk_size=1)

    with Session(pre_view_engine) as session:
        v1_count = session.scalar(sa.select(sa.func.count()).select_from(models.UsageEvent))
        v2_count = session.scalar(sa.select(sa.func.count()).select_from(models.UsageEventV2))
    assert v1_count == v2_count == 4


def test_downgrade_removes_only_backfilled(pre_view_engine: sa.Engine) -> None:
    with Session(pre_view_engine) as session:
        _add_v1(session, "a1")
        session.commit()
    # Insert the native (non-backfilled) v2 row via an explicit-column core
    # insert so it does not reference columns absent at this pre-view revision.
    table = models.UsageEventV2.__table__
    with pre_view_engine.begin() as connection:
        connection.execute(
            sa.insert(table).values(
                provider="anthropic",
                event_id="native-1",
                schema_version=2,
                event_kind="attempt",
                finality="final",
                sequence=0,
                native_model="claude-haiku-4-5",
                ts_started=_TS,
                success=True,
                provenance="official",
                dimensions={},
                extra={"gateway": {}},
            )
        )

    _backfill(pre_view_engine)
    with pre_view_engine.begin() as connection:
        removed = remove_backfilled_rows(connection)
    assert removed == 1

    def _count(connection: sa.Connection, event_id: str) -> int:
        return int(
            connection.execute(
                sa.select(sa.func.count())
                .select_from(table)
                .where(table.c.provider == "anthropic", table.c.event_id == event_id)
            ).scalar_one()
        )

    with pre_view_engine.connect() as connection:
        assert _count(connection, "a1") == 0  # backfilled row removed
        assert _count(connection, "native-1") == 1  # native row survives


def test_verify_detects_mismatch(pre_view_engine: sa.Engine) -> None:
    with Session(pre_view_engine) as session:
        _add_v1(session, "a1")
        session.commit()
    _backfill(pre_view_engine)

    # Corrupt a backfilled row so the token sums no longer match v1.
    with pre_view_engine.begin() as connection:
        connection.execute(
            sa.update(models.UsageEventV2)
            .where(models.UsageEventV2.event_id == "a1")
            .values(output_tokens=99999)
        )

    with pre_view_engine.connect() as connection:
        report = verify_backfill(connection)
    assert not report.ok
    assert report.mismatches
