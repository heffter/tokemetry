"""HTTP tests for API token management and the live WebSocket stream."""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

_MACHINE = {"name": "box-1"}


def test_token_lifecycle(client: TestClient, auth: dict[str, str]) -> None:
    created = client.post("/api/v1/tokens", json={"label": "openclaw"}, headers=auth)
    assert created.status_code == 201
    token = created.json()["token"]
    assert token.startswith("tkm_")

    # The minted token authenticates a protected endpoint.
    used = client.get("/api/v1/machines", headers={"Authorization": f"Bearer {token}"})
    assert used.status_code == 200

    listing = client.get("/api/v1/tokens", headers=auth).json()
    assert any(entry["label"] == "openclaw" for entry in listing)
    assert all("token" not in entry for entry in listing)


def test_duplicate_label_conflicts(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/api/v1/tokens", json={"label": "dup"}, headers=auth)
    again = client.post("/api/v1/tokens", json={"label": "dup"}, headers=auth)
    assert again.status_code == 409


def test_revoked_token_rejected(client: TestClient, auth: dict[str, str]) -> None:
    created = client.post("/api/v1/tokens", json={"label": "temp"}, headers=auth).json()
    token = created["token"]

    revoke = client.delete("/api/v1/tokens/temp", headers=auth)
    assert revoke.status_code == 204

    after = client.get("/api/v1/machines", headers={"Authorization": f"Bearer {token}"})
    assert after.status_code == 401


def test_revoke_unknown_label_404(client: TestClient, auth: dict[str, str]) -> None:
    assert client.delete("/api/v1/tokens/nope", headers=auth).status_code == 404


def test_stream_rejects_bad_token(client: TestClient) -> None:
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/api/v1/stream?token=wrong"),
    ):
        pass


def test_stream_delivers_ingest_event(client: TestClient, auth: dict[str, str]) -> None:
    token = auth["Authorization"].removeprefix("Bearer ")
    with client.websocket_connect(f"/api/v1/stream?token={token}") as ws:
        client.post(
            "/api/v1/ingest/limits",
            json={
                "machine": _MACHINE,
                "snapshots": [
                    {
                        "provider": "anthropic",
                        "ts": "2026-07-09T15:00:00+00:00",
                        "window_kind": "five_hour",
                        "utilization_pct": 10.0,
                    }
                ],
            },
            headers=auth,
        )
        message = ws.receive_json()
    assert message["type"] == "limits"
    assert message["machine"] == "box-1"
