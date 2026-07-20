"""Integration tests for the v2 sources API and token hash non-exposure."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from tokemetry_server.scopes import INGEST_EVENTS, QUERY_READ


def _event(
    event_id: str, source_name: str = "proxy", source_type: str = "gateway"
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "event_id": event_id,
        "event_kind": "attempt",
        "finality": "final",
        "sequence": 1,
        "provider": "anthropic",
        "native_model": "claude-sonnet-4-5",
        "ts_started": "2026-07-10T12:00:00Z",
        "output_tokens": 100,
        "source": {"type": source_type, "name": source_name, "version": "1.0"},
    }


def _ingest(client: TestClient, auth: dict[str, str], events: list[dict[str, Any]]) -> None:
    response = client.post(
        "/api/v2/ingest/events", json={"schema_version": 2, "events": events}, headers=auth
    )
    assert response.status_code == 200


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _make_token(client: TestClient, auth: dict[str, str], label: str, scopes: list[str]) -> str:
    response = client.post(
        "/api/v1/tokens", json={"label": label, "scopes": scopes}, headers=auth
    )
    token = response.json()["token"]
    assert isinstance(token, str)
    return token


def test_list_sources_with_health(client: TestClient, auth: dict[str, str]) -> None:
    _ingest(client, auth, [_event("anthropic:e1")])
    response = client.get("/api/v2/sources", headers=auth)
    assert response.status_code == 200
    sources = response.json()
    assert len(sources) == 1
    source = sources[0]
    assert source["type"] == "gateway"
    assert source["name"] == "proxy"
    assert source["health"]["stale"] is False  # just ingested
    assert source["health"]["last_successful_ingest"] is not None
    assert "token_hash" not in source  # never exposed


def test_list_sources_filters(client: TestClient, auth: dict[str, str]) -> None:
    _ingest(client, auth, [_event("anthropic:e1", source_name="proxy", source_type="gateway")])
    _ingest(
        client,
        auth,
        [_event("anthropic:e2", source_name="collector-x", source_type="collector")],
    )
    gateways = client.get("/api/v2/sources?type=gateway", headers=auth).json()
    assert {s["type"] for s in gateways} == {"gateway"}

    fresh = client.get("/api/v2/sources?stale=false", headers=auth).json()
    assert len(fresh) == 2
    stale = client.get("/api/v2/sources?stale=true", headers=auth).json()
    assert stale == []


def test_patch_label_and_billing_mode_preserves_identity(
    client: TestClient, auth: dict[str, str], read_engine: sa.Engine
) -> None:
    _ingest(client, auth, [_event("anthropic:e1")])
    source = client.get("/api/v2/sources", headers=auth).json()[0]
    source_id = source["id"]

    with read_engine.connect() as conn:
        before = conn.execute(
            sa.text("SELECT source_id FROM usage_events_v2 WHERE event_id = 'anthropic:e1'")
        ).scalar()

    response = client.patch(
        f"/api/v2/sources/{source_id}",
        json={"token_label": "renamed", "billing_mode": "subscription"},
        headers=auth,
    )
    assert response.status_code == 200
    assert response.json()["token_label"] == "renamed"
    assert response.json()["billing_mode"] == "subscription"

    with read_engine.connect() as conn:
        after = conn.execute(
            sa.text("SELECT source_id FROM usage_events_v2 WHERE event_id = 'anthropic:e1'")
        ).scalar()
    assert before == after == source_id  # event identity unchanged


def test_patch_rejects_invalid_billing_mode(client: TestClient, auth: dict[str, str]) -> None:
    _ingest(client, auth, [_event("anthropic:e1")])
    source_id = client.get("/api/v2/sources", headers=auth).json()[0]["id"]
    response = client.patch(
        f"/api/v2/sources/{source_id}", json={"billing_mode": "free"}, headers=auth
    )
    assert response.status_code == 400


def test_revoke_source_keeps_history(
    client: TestClient, auth: dict[str, str], read_engine: sa.Engine
) -> None:
    _ingest(client, auth, [_event("anthropic:e1")])
    source_id = client.get("/api/v2/sources", headers=auth).json()[0]["id"]
    response = client.post(f"/api/v2/sources/{source_id}/revoke", headers=auth)
    assert response.status_code == 200
    assert response.json()["revoked"] is True
    with read_engine.connect() as conn:
        count = conn.execute(
            sa.text("SELECT COUNT(*) FROM usage_events_v2 WHERE source_id = :sid"),
            {"sid": source_id},
        ).scalar()
    assert count == 1  # history retained


def test_unknown_source_404(client: TestClient, auth: dict[str, str]) -> None:
    assert client.patch("/api/v2/sources/999", json={}, headers=auth).status_code == 404
    assert client.post("/api/v2/sources/999/revoke", headers=auth).status_code == 404


def test_source_admin_requires_admin_scope(client: TestClient, auth: dict[str, str]) -> None:
    _ingest(client, auth, [_event("anthropic:e1")])
    source_id = client.get("/api/v2/sources", headers=auth).json()[0]["id"]
    query_token = _make_token(client, auth, "reader", [QUERY_READ])
    # A reader can list...
    assert client.get("/api/v2/sources", headers=_bearer(query_token)).status_code == 200
    # ...but not mutate or revoke.
    patch = client.patch(
        f"/api/v2/sources/{source_id}", json={"token_label": "x"}, headers=_bearer(query_token)
    )
    assert patch.status_code == 403


def test_token_list_never_exposes_hash(client: TestClient, auth: dict[str, str]) -> None:
    _make_token(client, auth, "some-token", [INGEST_EVENTS])
    listing = client.get("/api/v1/tokens", headers=auth).json()
    assert all("token_hash" not in row and "token" not in row for row in listing)
