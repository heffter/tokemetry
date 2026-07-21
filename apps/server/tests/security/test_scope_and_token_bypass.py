"""Scope and token bypass attempts (Task 70.8, PRD 18.6).

Forged and truncated tokens are rejected (401); a valid token is confined to
its scopes (403 outside them). Complements tests/integration/test_scope_enforcement.py
by focusing on the adversarial credentials.
"""

from __future__ import annotations

from typing import Any

from conftest import BOOTSTRAP_TOKEN
from fastapi.testclient import TestClient
from tokemetry_server.scopes import ADMIN_RETENTION, INGEST_EVENTS, QUERY_READ

_RANGE = {"from": "2026-07-01T00:00:00Z", "to": "2026-08-01T00:00:00Z"}
_USAGE = "/api/v2/usage"
_INGEST = "/api/v2/ingest/events"
_RETENTION = "/api/v2/admin/retention"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _mint(client: TestClient, auth: dict[str, str], label: str, scopes: list[str]) -> str:
    response = client.post(
        "/api/v1/tokens", json={"label": label, "scopes": scopes}, headers=auth
    )
    assert response.status_code == 201, response.text
    token = response.json()["token"]
    assert isinstance(token, str)
    return token


def _event() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "event_id": "anthropic:sec1",
        "event_kind": "attempt",
        "finality": "final",
        "sequence": 1,
        "provider": "anthropic",
        "native_model": "claude-sonnet-4-5",
        "ts_started": "2026-07-10T12:00:00Z",
        "output_tokens": 10,
        "source": {"type": "gateway", "name": "proxy", "version": "1"},
    }


def test_no_token_is_unauthorized(client: TestClient) -> None:
    assert client.get(_USAGE, params=_RANGE).status_code == 401


def test_forged_token_is_unauthorized(client: TestClient) -> None:
    forged = "tkm_" + "deadbeef" * 8
    assert client.get(_USAGE, params=_RANGE, headers=_bearer(forged)).status_code == 401


def test_truncated_bootstrap_token_is_unauthorized(client: TestClient) -> None:
    truncated = BOOTSTRAP_TOKEN[:-4]
    assert (
        client.get(_USAGE, params=_RANGE, headers=_bearer(truncated)).status_code == 401
    )


def test_query_token_cannot_ingest(client: TestClient, auth: dict[str, str]) -> None:
    reader = _mint(client, auth, "reader", [QUERY_READ])
    response = client.post(
        _INGEST,
        json={"schema_version": 2, "events": [_event()]},
        headers=_bearer(reader),
    )
    assert response.status_code == 403


def test_ingest_token_cannot_reach_admin(
    client: TestClient, auth: dict[str, str]
) -> None:
    ingester = _mint(client, auth, "ingester", [INGEST_EVENTS])
    assert client.get(_RETENTION, headers=_bearer(ingester)).status_code == 403


def test_query_token_cannot_reach_admin(
    client: TestClient, auth: dict[str, str]
) -> None:
    reader = _mint(client, auth, "reader2", [QUERY_READ])
    assert client.get(_RETENTION, headers=_bearer(reader)).status_code == 403


def test_correct_scope_is_allowed(client: TestClient, auth: dict[str, str]) -> None:
    admin = _mint(client, auth, "ret-admin", [ADMIN_RETENTION])
    assert client.get(_RETENTION, headers=_bearer(admin)).status_code == 200
    reader = _mint(client, auth, "reader3", [QUERY_READ])
    assert client.get(_USAGE, params=_RANGE, headers=_bearer(reader)).status_code == 200
