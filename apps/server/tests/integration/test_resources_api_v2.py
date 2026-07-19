"""v2 limits/data-quality/rollups read API: contract, scope, validation (Task 66.6)."""

from __future__ import annotations

import sqlalchemy as sa
from fastapi.testclient import TestClient
from tokemetry_server.scopes import INGEST_EVENTS

_RANGE = {"from": "2026-07-01T00:00:00Z", "to": "2026-08-01T00:00:00Z"}
_DAY_RANGE = {"from": "2026-07-01", "to": "2026-07-31"}


def test_data_quality_listing_and_filter(
    client: TestClient, auth: dict[str, str], read_engine: sa.Engine
) -> None:
    with read_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO data_quality_events (kind, subject, detail, ts, resolved) "
                "VALUES ('unknown_model', 'anthropic/x', '{}', "
                "'2026-07-10T12:00:00+00:00', 0)"
            )
        )
    listed = client.get("/api/v2/data-quality", headers=auth)
    assert listed.status_code == 200
    assert any(e["kind"] == "unknown_model" for e in listed.json()["events"])

    filtered = client.get("/api/v2/data-quality", params={"kind": "clock_skew"}, headers=auth)
    assert filtered.json()["events"] == []


def test_limits_endpoint_contract_and_range(client: TestClient, auth: dict[str, str]) -> None:
    response = client.get("/api/v2/limits", params=_RANGE, headers=auth)
    assert response.status_code == 200
    assert set(response.json().keys()) == {"limits", "next_cursor"}

    wide = client.get(
        "/api/v2/limits",
        params={"from": "2020-01-01T00:00:00Z", "to": "2026-01-01T00:00:00Z"},
        headers=auth,
    )
    assert wide.status_code == 400


def test_limits_forecast_endpoint(
    client: TestClient, auth: dict[str, str], read_engine: sa.Engine
) -> None:
    # A rising five-hour stream: 20% -> 60% over 40 minutes.
    with read_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO limit_snapshots "
                "(provider, ts, window_kind, utilization_pct, provenance, raw) VALUES "
                "('anthropic', '2026-07-10T12:00:00+00:00', 'five_hour', 20.0, 'official', '{}'),"
                "('anthropic', '2026-07-10T12:40:00+00:00', 'five_hour', 60.0, 'official', '{}')"
            )
        )
    response = client.get("/api/v2/limits/forecast", params=_RANGE, headers=auth)
    assert response.status_code == 200
    forecasts = response.json()["forecasts"]
    assert len(forecasts) == 1
    forecast = forecasts[0]
    assert forecast["stream"]["provider"] == "anthropic"
    assert forecast["stream"]["window_kind"] == "five_hour"
    assert forecast["utilization_pct"] == 60.0
    assert forecast["predicted_exhaustion_at"] is not None
    assert forecast["confidence"] in {"low", "medium", "high"}

    wide = client.get(
        "/api/v2/limits/forecast",
        params={"from": "2020-01-01T00:00:00Z", "to": "2026-01-01T00:00:00Z"},
        headers=auth,
    )
    assert wide.status_code == 400


def test_rollups_endpoint_contract_and_range(client: TestClient, auth: dict[str, str]) -> None:
    response = client.get("/api/v2/rollups", params=_DAY_RANGE, headers=auth)
    assert response.status_code == 200
    assert set(response.json().keys()) == {"rollups", "next_cursor"}

    reversed_range = client.get(
        "/api/v2/rollups", params={"from": "2026-07-31", "to": "2026-07-01"}, headers=auth
    )
    assert reversed_range.status_code == 400


def test_resources_require_query_read_scope(client: TestClient, auth: dict[str, str]) -> None:
    token = client.post(
        "/api/v1/tokens", json={"label": "ingest", "scopes": [INGEST_EVENTS]}, headers=auth
    ).json()["token"]
    bearer = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v2/limits", params=_RANGE, headers=bearer).status_code == 403
    assert client.get("/api/v2/data-quality", headers=bearer).status_code == 403
    assert client.get("/api/v2/rollups", params=_DAY_RANGE, headers=bearer).status_code == 403
