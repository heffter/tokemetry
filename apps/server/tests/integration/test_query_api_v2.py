"""v2 usage/cost query API: contract, scope, and validation (Task 66.4)."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from tokemetry_server.scopes import INGEST_EVENTS

_RANGE = {"from": "2026-07-01T00:00:00Z", "to": "2026-08-01T00:00:00Z"}


def _event(
    event_id: str, provider: str = "anthropic", model: str = "claude-sonnet-4-5"
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "event_id": event_id,
        "event_kind": "attempt",
        "finality": "final",
        "sequence": 1,
        "provider": provider,
        "native_model": model,
        "ts_started": "2026-07-10T12:00:00Z",
        "input_tokens": 1000,
        "source": {"type": "gateway", "name": "proxy", "version": "1.0"},
    }


def _ingest(client: TestClient, auth: dict[str, str], events: list[dict[str, Any]]) -> None:
    response = client.post(
        "/api/v2/ingest/events", json={"schema_version": 2, "events": events}, headers=auth
    )
    assert response.status_code == 200


def test_usage_grouped_by_provider(client: TestClient, auth: dict[str, str]) -> None:
    _ingest(client, auth, [_event("a:1"), _event("o:1", provider="openai", model="gpt-5")])
    response = client.get("/api/v2/usage", params={**_RANGE, "group_by": "provider"}, headers=auth)
    assert response.status_code == 200
    body = response.json()
    assert body["group_by"] == "provider"
    keys = {row["key"] for row in body["rows"]}
    assert keys == {"anthropic", "openai"}
    assert all(row["attempt_count"] == 1 for row in body["rows"])
    assert "warnings" in body


def test_usage_rejects_bad_group_by_and_range(client: TestClient, auth: dict[str, str]) -> None:
    bad = client.get("/api/v2/usage", params={**_RANGE, "group_by": "galaxy"}, headers=auth)
    assert bad.status_code == 400
    wide = client.get(
        "/api/v2/usage",
        params={"from": "2020-01-01T00:00:00Z", "to": "2026-01-01T00:00:00Z"},
        headers=auth,
    )
    assert wide.status_code == 400  # exceeds the max range


def test_costs_returns_dual_metric_shape(client: TestClient, auth: dict[str, str]) -> None:
    _ingest(client, auth, [_event("a:1")])
    response = client.get("/api/v2/costs", params={**_RANGE, "group_by": "provider"}, headers=auth)
    assert response.status_code == 200
    body = response.json()
    # The response separates the two cost series and never exposes a merged total.
    assert set(body.keys()) == {"group_by", "rows", "warnings"}
    for row in body["rows"]:
        assert "actual_spend_usd" in row and "subscription_value_usd" in row
        assert "total_usd" not in row


def test_reconciliation_endpoint(client: TestClient, auth: dict[str, str]) -> None:
    response = client.get("/api/v2/costs/reconciliation", params=_RANGE, headers=auth)
    assert response.status_code == 200
    assert response.json() == {"rows": []}  # no observed costs yet


def test_query_requires_query_read_scope(client: TestClient, auth: dict[str, str]) -> None:
    token = client.post(
        "/api/v1/tokens", json={"label": "ingest-only", "scopes": [INGEST_EVENTS]}, headers=auth
    ).json()["token"]
    forbidden = client.get(
        "/api/v2/usage",
        params={**_RANGE, "group_by": "provider"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert forbidden.status_code == 403
