"""The v1 compatibility view over usage_events_v2 (subtask 62.10)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session
from tokemetry_server.db import models

_TS = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)


def _v2_attempt(session: Session, event_id: str, **overrides: object) -> None:
    """Insert a v1-mirrored v2 attempt row (with the ``_v1`` namespace)."""
    defaults: dict[str, object] = {
        "provider": "anthropic",
        "event_id": event_id,
        "schema_version": 2,
        "event_kind": "attempt",
        "finality": "final",
        "sequence": 0,
        "native_model": "claude-sonnet-4-5",
        "ts_started": _TS,
        "ts_completed": _TS,
        "machine": "devbox-01",
        "session_id": "sess-1",
        "project": "proj",
        "input_tokens": 100,
        "output_tokens": 50,
        "success": True,
        "provenance": "official",
        "cost_usd": Decimal("1.2345"),
        "dimensions": {},
        "extra": {
            "web_search": 1,
            "_v1": {
                "git_branch": "main",
                "client_version": "1.0",
                "entrypoint": "cli",
                "is_sidechain": True,
                "session_kind": "interactive",
                "speed": "fast",
                "source": "collector",
            },
            "_backfill": True,
        },
    }
    defaults.update(overrides)
    session.add(models.UsageEventV2(**defaults))


def test_view_projects_v1_shape(migrated_engine: sa.Engine) -> None:
    with Session(migrated_engine) as session:
        _v2_attempt(session, "a1")
        session.commit()

    with Session(migrated_engine) as session:
        row = session.get(models.UsageEvent, ("anthropic", "a1"))
        assert row is not None
        # SQLite reads back naive UTC; compare on the wall clock.
        assert row.ts.replace(tzinfo=None) == _TS.replace(tzinfo=None)
        assert row.model == "claude-sonnet-4-5"
        assert row.cost_usd == Decimal("1.2345")
        assert row.is_sidechain is True
        assert row.git_branch == "main"
        assert row.source == "collector"
        assert row.output_tokens == 50
        # extra is cleaned of the internal keys.
        assert row.extra == {"web_search": 1}


def test_view_excludes_non_attempt_kinds(migrated_engine: sa.Engine) -> None:
    with Session(migrated_engine) as session:
        _v2_attempt(session, "a1")
        _v2_attempt(session, "lr1", event_kind="logical_request")
        session.commit()

    with Session(migrated_engine) as session:
        count = session.scalar(sa.select(sa.func.count()).select_from(models.UsageEvent))
        assert count == 1


def test_view_grafana_raw_sql(migrated_engine: sa.Engine) -> None:
    """A Grafana-style raw SQL query returns the v1-shaped rows."""
    with Session(migrated_engine) as session:
        _v2_attempt(session, "a1", output_tokens=70)
        session.commit()

    with migrated_engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                "SELECT provider, model, output_tokens, cost_usd "
                "FROM usage_events ORDER BY event_id"
            )
        ).all()
    assert len(rows) == 1
    assert rows[0].provider == "anthropic"
    assert rows[0].model == "claude-sonnet-4-5"
    assert rows[0].output_tokens == 70
