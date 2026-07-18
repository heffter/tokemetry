"""v1 ingest mirrors into the v2 ledger via the revision engine (keep-max)."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from tokemetry_server.db import models


def _v1_event(event_id: str = "req-1", **overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event_id": event_id,
        "provider": "anthropic",
        "native_model": "claude-sonnet-4-5",
        "ts": "2026-07-10T12:00:00Z",
        "session_id": "sess-1",
        "project": "proj",
        "is_sidechain": True,
        "input_tokens": 100,
        "output_tokens": 50,
    }
    event.update(overrides)
    return event


def _ingest(client: TestClient, auth: dict[str, str], events: list[dict[str, Any]]) -> None:
    response = client.post(
        "/api/v1/ingest/events",
        json={"machine": {"name": "devbox-01"}, "events": events},
        headers=auth,
    )
    assert response.status_code == 200


def _v2_row(engine: sa.Engine, event_id: str) -> models.UsageEventV2 | None:
    with Session(engine) as session:
        return session.get(models.UsageEventV2, ("anthropic", event_id))


def test_v1_ingest_mirrors_into_v2(
    client: TestClient, auth: dict[str, str], read_engine: sa.Engine
) -> None:
    _ingest(client, auth, [_v1_event("req-1")])

    row = _v2_row(read_engine, "req-1")
    assert row is not None
    assert row.event_kind == "attempt"
    assert row.finality == "final"
    assert row.sequence == 0
    assert row.native_model == "claude-sonnet-4-5"
    assert row.output_tokens == 50
    assert row.reasoning_tokens == 0
    assert row.extra["_v1"]["is_sidechain"] is True


def test_v2_mirror_preserves_cost_matching_v1(
    client: TestClient, auth: dict[str, str], read_engine: sa.Engine
) -> None:
    _ingest(client, auth, [_v1_event("req-cost")])

    with Session(read_engine) as session:
        v1_row = session.get(models.UsageEvent, ("anthropic", "req-cost"))
        v2_row = session.get(models.UsageEventV2, ("anthropic", "req-cost"))
    assert v1_row is not None and v2_row is not None
    # Cost is computed once from the event; both rows must carry the same value.
    assert v2_row.cost_usd == v1_row.cost_usd


def test_keep_max_dedupe_matches_between_tables(
    client: TestClient, auth: dict[str, str], read_engine: sa.Engine
) -> None:
    # A later, larger-output snapshot wins in both tables (keep-max).
    _ingest(client, auth, [_v1_event("req-km", output_tokens=50)])
    _ingest(client, auth, [_v1_event("req-km", output_tokens=120)])
    # A lower-output replay is ignored in both tables.
    _ingest(client, auth, [_v1_event("req-km", output_tokens=10)])

    with Session(read_engine) as session:
        v1_row = session.get(models.UsageEvent, ("anthropic", "req-km"))
        v2_row = session.get(models.UsageEventV2, ("anthropic", "req-km"))
    assert v1_row is not None and v2_row is not None
    assert v1_row.output_tokens == 120
    assert v2_row.output_tokens == 120
