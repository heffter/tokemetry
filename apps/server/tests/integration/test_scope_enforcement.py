"""Scope enforcement across v1, v2, and the WebSocket stream (task 63.4)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from tokemetry_server.scopes import INGEST_EVENTS, QUERY_READ


def _make_token(
    client: TestClient,
    auth: dict[str, str],
    label: str,
    scopes: list[str],
    source_allowlist: list[str] | None = None,
) -> str:
    response = client.post(
        "/api/v1/tokens",
        json={"label": label, "scopes": scopes, "source_allowlist": source_allowlist},
        headers=auth,
    )
    assert response.status_code == 201
    return response.json()["token"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _event(event_id: str = "anthropic:req_1", source_name: str = "proxy") -> dict[str, Any]:
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
        "source": {"type": "gateway", "name": source_name, "version": "1.0"},
    }


def test_ingest_only_token_denied_on_query(client: TestClient, auth: dict[str, str]) -> None:
    token = _make_token(client, auth, "ingest-only", [INGEST_EVENTS])
    # Allowed on its ingest scope.
    ingest = client.post(
        "/api/v2/ingest/events",
        json={"schema_version": 2, "events": [_event()]},
        headers=_bearer(token),
    )
    assert ingest.status_code == 200
    # Denied on query endpoints (FR-INGEST-004).
    assert client.get("/api/v2/providers", headers=_bearer(token)).status_code == 403


def test_query_only_token_denied_on_ingest(client: TestClient, auth: dict[str, str]) -> None:
    token = _make_token(client, auth, "query-only", [QUERY_READ])
    assert client.get("/api/v2/providers", headers=_bearer(token)).status_code == 200
    denied = client.post(
        "/api/v2/ingest/events",
        json={"schema_version": 2, "events": [_event()]},
        headers=_bearer(token),
    )
    assert denied.status_code == 403


def test_websocket_requires_query_read(client: TestClient, auth: dict[str, str]) -> None:
    ingest_token = _make_token(client, auth, "ws-ingest", [INGEST_EVENTS])
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(f"/api/v1/stream?token={ingest_token}"),
    ):
        pass

    query_token = _make_token(client, auth, "ws-query", [QUERY_READ])
    with client.websocket_connect(f"/api/v1/stream?token={query_token}"):
        pass  # accepted


def test_source_allowlist_accept_and_reject(client: TestClient, auth: dict[str, str]) -> None:
    token = _make_token(
        client, auth, "allowlisted", [INGEST_EVENTS], source_allowlist=["proxy-a"]
    )
    ok = client.post(
        "/api/v2/ingest/events",
        json={"schema_version": 2, "events": [_event(source_name="proxy-a")]},
        headers=_bearer(token),
    )
    assert ok.status_code == 200

    denied = client.post(
        "/api/v2/ingest/events",
        json={"schema_version": 2, "events": [_event(source_name="proxy-b")]},
        headers=_bearer(token),
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["errors"][0]["code"] == "source_not_allowed"


def test_correction_requires_admin_scope(client: TestClient, auth: dict[str, str]) -> None:
    token = _make_token(client, auth, "no-correction", [INGEST_EVENTS])
    response = client.post(
        "/api/v2/ingest/events",
        json={"schema_version": 2, "events": [_event()], "correction": True},
        headers=_bearer(token),
    )
    assert response.status_code == 403


def test_revoked_and_unknown_tokens_return_uniform_401(
    client: TestClient, auth: dict[str, str]
) -> None:
    token = _make_token(client, auth, "to-revoke", [QUERY_READ])
    client.delete("/api/v1/tokens/to-revoke", headers=auth)

    revoked = client.get("/api/v2/providers", headers=_bearer(token))
    unknown = client.get("/api/v2/providers", headers=_bearer("tkm_does_not_exist"))
    assert revoked.status_code == 401
    assert unknown.status_code == 401
    # Uniform response: neither reveals whether a token/label exists (FR-SEC-010).
    assert revoked.json() == unknown.json()
