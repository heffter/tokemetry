"""Integration tests for daily rollup refresh, driven through the HTTP layer."""

from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient

_MACHINE = {"name": "box-1", "platform": "linux", "collector_version": "0.1.0"}


def _event(event_id: str, model: str = "claude-opus-4-5", **overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event_id": event_id,
        "provider": "anthropic",
        "native_model": model,
        "ts": "2026-07-09T09:41:14+00:00",
        "project": "proj-a",
        "input_tokens": 1_000_000,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_short_tokens": 0,
        "cache_write_long_tokens": 0,
    }
    event.update(overrides)
    return event


def _post(client: TestClient, auth: dict[str, str], *events: dict[str, Any]) -> None:
    response = client.post(
        "/api/v1/ingest/events",
        json={"machine": _MACHINE, "events": list(events)},
        headers=auth,
    )
    assert response.status_code == 200


def test_rollup_created_on_ingest(
    client: TestClient, auth: dict[str, str], read_engine: sa.Engine
) -> None:
    _post(client, auth, _event("req_1"), _event("req_2"))

    with read_engine.connect() as conn:
        row = conn.execute(
            sa.text(
                "SELECT input_tokens, total_tokens, provenance, cost_usd "
                "FROM daily_rollups WHERE model='claude-opus-4-5' AND project='proj-a'"
            )
        ).one()
    assert row[0] == 2_000_000  # two events summed
    assert row[1] == 2_000_000
    assert row[2] == "derived"
    assert float(row[3]) > 0  # cost aggregated from priced events


def test_rollup_splits_by_grain(
    client: TestClient, auth: dict[str, str], read_engine: sa.Engine
) -> None:
    _post(
        client,
        auth,
        _event("req_1", project="proj-a"),
        _event("req_2", project="proj-b"),
        _event("req_3", model="claude-sonnet-4-5", project="proj-a"),
    )

    with read_engine.connect() as conn:
        count = conn.execute(sa.text("SELECT COUNT(*) FROM daily_rollups")).scalar_one()
    assert count == 3  # (opus,proj-a), (opus,proj-b), (sonnet,proj-a)


def test_rollup_reflects_keep_max_update(
    client: TestClient, auth: dict[str, str], read_engine: sa.Engine
) -> None:
    _post(client, auth, _event("req_1", output_tokens=10))
    # A settled record with more output supersedes the earlier snapshot.
    _post(client, auth, _event("req_1", output_tokens=500))

    with read_engine.connect() as conn:
        row = conn.execute(
            sa.text("SELECT output_tokens FROM daily_rollups WHERE model='claude-opus-4-5'")
        ).one()
    # Rollup must reflect the kept-max event, not the sum of both sends.
    assert row[0] == 500


def test_rollup_idempotent_on_reingest(
    client: TestClient, auth: dict[str, str], read_engine: sa.Engine
) -> None:
    _post(client, auth, _event("req_1"))
    _post(client, auth, _event("req_1"))

    with read_engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT input_tokens FROM daily_rollups WHERE model='claude-opus-4-5'")
        ).all()
    assert len(rows) == 1
    assert rows[0][0] == 1_000_000  # not doubled
