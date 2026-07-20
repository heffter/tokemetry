"""v2 pricing administration API: reprice/revert and scope enforcement (64.6).

The endpoints ingest through the same app as the rest of the suite, so these
tests drive the full path: ingest a v2 event, reprice its range under the
``admin:pricing`` scope, then revert. Non-admin callers are rejected.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from tokemetry_server.scopes import ADMIN_PRICING, QUERY_READ

_RANGE = {"start": "2026-07-01T00:00:00Z", "end": "2026-08-01T00:00:00Z"}


def _event(event_id: str) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "event_id": event_id,
        "event_kind": "attempt",
        "finality": "final",
        "sequence": 1,
        "provider": "anthropic",
        "native_model": "claude-sonnet-4-5",
        "ts_started": "2026-07-10T12:00:00Z",
        "input_tokens": 1000,
        "source": {"type": "gateway", "name": "proxy", "version": "1.0"},
    }


def _ingest(client: TestClient, auth: dict[str, str], event_id: str) -> None:
    response = client.post(
        "/api/v2/ingest/events",
        json={"schema_version": 2, "events": [_event(event_id)]},
        headers=auth,
    )
    assert response.status_code == 200


def _make_token(
    client: TestClient, auth: dict[str, str], label: str, scopes: list[str]
) -> str:
    response = client.post(
        "/api/v1/tokens", json={"label": label, "scopes": scopes}, headers=auth
    )
    token = response.json()["token"]
    assert isinstance(token, str)
    return token


def test_reprice_then_revert_round_trip(client: TestClient, auth: dict[str, str]) -> None:
    _ingest(client, auth, "anthropic:e1")

    # The first reprice creates the baseline cost row under version "2"; the
    # second retains it and makes "3" active. Revert then re-activates "2".
    first = client.post("/api/v2/pricing/reprice", json=_RANGE, headers=auth)
    assert first.status_code == 200
    assert first.json() == {"pricing_version": "2", "affected": 1}

    second = client.post("/api/v2/pricing/reprice", json=_RANGE, headers=auth)
    assert second.json() == {"pricing_version": "3", "affected": 1}

    revert = client.post(
        "/api/v2/pricing/revert",
        json={"pricing_version": "2", **_RANGE},
        headers=auth,
    )
    assert revert.status_code == 200
    assert revert.json() == {"pricing_version": "2", "affected": 1}


def test_reprice_requires_admin_pricing_scope(
    client: TestClient, auth: dict[str, str]
) -> None:
    _ingest(client, auth, "anthropic:e1")
    reader_token = _make_token(client, auth, "reader", [QUERY_READ])
    reader = {"Authorization": f"Bearer {reader_token}"}
    forbidden = client.post("/api/v2/pricing/reprice", json=_RANGE, headers=reader)
    assert forbidden.status_code == 403

    admin_token = _make_token(client, auth, "pricer", [ADMIN_PRICING])
    allowed = client.post(
        "/api/v2/pricing/reprice",
        json=_RANGE,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert allowed.status_code == 200


def test_reprice_rejects_unknown_fields(client: TestClient, auth: dict[str, str]) -> None:
    response = client.post(
        "/api/v2/pricing/reprice",
        json={**_RANGE, "unexpected": True},
        headers=auth,
    )
    assert response.status_code == 422
