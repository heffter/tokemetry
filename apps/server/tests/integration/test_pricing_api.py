"""HTTP tests for the pricing write and recompute endpoints."""

import sqlalchemy as sa
from fastapi.testclient import TestClient

_MACHINE = {"name": "box-1"}

_UNPRICED_EVENT = {
    "event_id": "req_1",
    "provider": "anthropic",
    "native_model": "claude-fable-5",  # not in the default price table
    "ts": "2026-07-09T12:00:00+00:00",
    "input_tokens": 1_000_000,
    "output_tokens": 0,
}

_PRICE = {
    "provider": "anthropic",
    "model": "claude-fable-5",
    "effective_date": "2026-01-01",
    "input_per_mtok": "7",
    "output_per_mtok": "35",
    "cache_read_per_mtok": "0.7",
    "cache_write_short_per_mtok": "8.75",
    "cache_write_long_per_mtok": "14",
    "source": "manual",
}


def test_requires_auth(client: TestClient) -> None:
    assert client.post("/api/v1/pricing", json=_PRICE).status_code == 401
    assert client.post("/api/v1/pricing/recompute").status_code == 401


def test_create_price_appears_in_listing(client: TestClient, auth: dict[str, str]) -> None:
    created = client.post("/api/v1/pricing", json=_PRICE, headers=auth)
    assert created.status_code == 201
    assert created.json()["model"] == "claude-fable-5"

    listing = client.get("/api/v1/pricing", headers=auth).json()
    assert any(row["model"] == "claude-fable-5" and row["source"] == "manual" for row in listing)


def test_recompute_fills_previously_unpriced_cost(
    client: TestClient, auth: dict[str, str], read_engine: sa.Engine
) -> None:
    # Ingest an event for a model with no price -> cost is null.
    client.post(
        "/api/v1/ingest/events",
        json={"machine": _MACHINE, "events": [_UNPRICED_EVENT]},
        headers=auth,
    )
    with read_engine.connect() as conn:
        before = conn.execute(sa.text("SELECT cost_usd FROM usage_events")).scalar_one()
    assert before is None

    # Add the price, then recompute.
    client.post("/api/v1/pricing", json=_PRICE, headers=auth)
    result = client.post("/api/v1/pricing/recompute", headers=auth)
    assert result.status_code == 200
    assert result.json()["events_updated"] == 1

    with read_engine.connect() as conn:
        after = conn.execute(sa.text("SELECT cost_usd FROM usage_events")).scalar_one()
        rollup = conn.execute(
            sa.text("SELECT cost_usd FROM daily_rollups WHERE model='claude-fable-5'")
        ).scalar_one()
    assert after is not None
    assert float(after) == 7.0  # 1M input tokens at $7/MTok
    assert rollup is not None
    assert float(rollup) == 7.0  # rollup cost refreshed too


def test_recompute_updates_future_ingest_pricing(
    client: TestClient, auth: dict[str, str], read_engine: sa.Engine
) -> None:
    client.post("/api/v1/pricing", json=_PRICE, headers=auth)
    client.post("/api/v1/pricing/recompute", headers=auth)

    # A NEW event for the now-priced model should be costed at ingest.
    client.post(
        "/api/v1/ingest/events",
        json={
            "machine": _MACHINE,
            "events": [{**_UNPRICED_EVENT, "event_id": "req_2"}],
        },
        headers=auth,
    )
    with read_engine.connect() as conn:
        cost = conn.execute(
            sa.text("SELECT cost_usd FROM usage_events WHERE event_id='req_2'")
        ).scalar_one()
    assert cost is not None
    assert float(cost) == 7.0
