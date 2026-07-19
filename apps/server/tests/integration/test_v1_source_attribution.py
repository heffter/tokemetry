"""v1 collector traffic is attributed to a derived source (task 63.6)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from tokemetry_server.db import models
from tokemetry_server.db.backfill import (
    COLLECTOR_SOURCE_NAME,
    attribute_backfilled_sources,
)

_TS = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)


def _v1_event(event_id: str) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "provider": "anthropic",
        "native_model": "claude-sonnet-4-5",
        "ts": "2026-07-10T12:00:00Z",
        "output_tokens": 100,
    }


def _ingest(client: TestClient, auth: dict[str, str], machine: str, events: list[dict]) -> None:
    response = client.post(
        "/api/v1/ingest/events",
        json={"machine": {"name": machine, "collector_version": "3.1.0"}, "events": events},
        headers=auth,
    )
    assert response.status_code == 200


def test_v1_ingest_creates_one_source_per_machine(
    client: TestClient, auth: dict[str, str], read_engine: sa.Engine
) -> None:
    _ingest(client, auth, "devbox-01", [_v1_event("req-1")])
    _ingest(client, auth, "devbox-01", [_v1_event("req-2")])  # same machine, new batch

    with Session(read_engine) as session:
        sources = session.execute(sa.select(models.Source)).scalars().all()
        assert len(sources) == 1  # one derived source, reused across batches
        source = sources[0]
        assert source.type == "collector"
        assert source.name == COLLECTOR_SOURCE_NAME
        assert source.instance_id == "devbox-01"
        assert source.machine == "devbox-01"
        assert source.version == "3.1.0"
        assert source.last_successful_ingest is not None  # health recorded

        rows = session.execute(sa.select(models.UsageEventV2)).scalars().all()
        assert {row.source_id for row in rows} == {source.id}


def test_two_machines_get_distinct_sources(
    client: TestClient, auth: dict[str, str], read_engine: sa.Engine
) -> None:
    _ingest(client, auth, "devbox-01", [_v1_event("req-1")])
    _ingest(client, auth, "laptop-02", [_v1_event("req-2")])
    with Session(read_engine) as session:
        instances = {
            s.instance_id
            for s in session.execute(sa.select(models.Source)).scalars().all()
        }
    assert instances == {"devbox-01", "laptop-02"}


def test_backfill_attribution_of_historical_rows(migrated_engine: sa.Engine) -> None:
    # Simulate rows left source-less by the 62.8 backfill, plus their machine.
    with Session(migrated_engine) as session:
        session.add(
            models.Machine(id="oldbox", collector_version="2.0.0", first_seen=_TS, last_seen=_TS)
        )
        session.add(
            models.UsageEventV2(
                provider="anthropic",
                event_id="hist-1",
                schema_version=2,
                event_kind="attempt",
                finality="final",
                sequence=0,
                native_model="claude-sonnet-4-5",
                ts_started=_TS,
                machine="oldbox",
                success=True,
                provenance="local_estimate",
                dimensions={},
                extra={"_backfill": True},
            )
        )
        session.commit()

    with migrated_engine.begin() as connection:
        attributed = attribute_backfilled_sources(connection)
    assert attributed == 1

    with Session(migrated_engine) as session:
        source = session.execute(
            sa.select(models.Source).where(models.Source.instance_id == "oldbox")
        ).scalar_one()
        assert source.version == "2.0.0"
        row = session.get(models.UsageEventV2, ("anthropic", "hist-1"))
        assert row is not None
        assert row.source_id == source.id


def test_v1_ingest_still_registers_machine(
    client: TestClient, auth: dict[str, str], read_engine: sa.Engine
) -> None:
    """FR-SOURCE-008: the machines table stays fully supported alongside sources."""
    _ingest(client, auth, "devbox-01", [_v1_event("req-1")])
    with Session(read_engine) as session:
        machine = session.get(models.Machine, "devbox-01")
        assert machine is not None
        assert machine.collector_version == "3.1.0"
