"""Revoked-token behavior, including in-flight WebSocket disconnection (Task 70.8).

A revoked token is refused on REST and on the stream, both at connect time and
mid-connection (NFR-SEC-008).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import BOOTSTRAP_TOKEN
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from tokemetry_server.app import create_app
from tokemetry_server.config import Settings
from tokemetry_server.scopes import QUERY_READ

_RANGE = {"from": "2026-07-01T00:00:00Z", "to": "2026-08-01T00:00:00Z"}


def _mint(client: TestClient, auth: dict[str, str], label: str) -> str:
    response = client.post(
        "/api/v1/tokens", json={"label": label, "scopes": [QUERY_READ]}, headers=auth
    )
    assert response.status_code == 201, response.text
    token = response.json()["token"]
    assert isinstance(token, str)
    return token


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_revoked_token_refused_on_rest(client: TestClient, auth: dict[str, str]) -> None:
    token = _mint(client, auth, "rest-revoke")
    assert client.get("/api/v2/usage", params=_RANGE, headers=_bearer(token)).status_code == 200
    client.delete("/api/v1/tokens/rest-revoke", headers=auth)
    assert client.get("/api/v2/usage", params=_RANGE, headers=_bearer(token)).status_code == 401


def test_revoked_token_refused_at_ws_connect(
    client: TestClient, auth: dict[str, str]
) -> None:
    token = _mint(client, auth, "ws-connect-revoke")
    client.delete("/api/v1/tokens/ws-connect-revoke", headers=auth)
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(f"/api/v1/stream?token={token}"),
    ):
        pass


def test_revoked_token_disconnected_in_flight(tmp_path: Path) -> None:
    """A token revoked while its stream is open is dropped within the re-check."""
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'ws.db'}",
        api_bootstrap_token=BOOTSTRAP_TOKEN,
        seed_default_alerts=False,
        cost_worker_enabled=False,
        ws_reauth_interval_seconds=0.05,
    )
    auth = {"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"}
    with TestClient(create_app(settings=settings)) as client:
        token = _mint(client, auth, "ws-inflight")
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect(f"/api/v1/stream?token={token}") as ws,
        ):
            # Revoke after the connection is established; the server's periodic
            # re-auth then closes it, so the next receive raises.
            client.delete("/api/v1/tokens/ws-inflight", headers=auth)
            ws.receive_json()
