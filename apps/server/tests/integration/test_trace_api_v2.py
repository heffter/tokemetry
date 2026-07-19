"""v2 attempt/request/session query API: contract, scope, validation (Task 66.5)."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from tokemetry_server.scopes import INGEST_EVENTS

_RANGE = {"from": "2026-07-01T00:00:00Z", "to": "2026-08-01T00:00:00Z"}


def _event(event_id: str, ts: str = "2026-07-10T12:00:00Z") -> dict[str, Any]:
    return {
        "schema_version": 2,
        "event_id": event_id,
        "event_kind": "attempt",
        "finality": "final",
        "sequence": 1,
        "provider": "anthropic",
        "native_model": "claude-sonnet-4-5",
        "ts_started": ts,
        "input_tokens": 1000,
        "session_id": "sess-1",
        "source": {"type": "gateway", "name": "proxy", "version": "1.0"},
    }


def _ingest(client: TestClient, auth: dict[str, str], events: list[dict[str, Any]]) -> None:
    response = client.post(
        "/api/v2/ingest/events", json={"schema_version": 2, "events": events}, headers=auth
    )
    assert response.status_code == 200


def test_attempts_listing_and_pagination(client: TestClient, auth: dict[str, str]) -> None:
    _ingest(client, auth, [
        _event("e1", "2026-07-10T12:00:00Z"),
        _event("e2", "2026-07-10T12:01:00Z"),
    ])
    first = client.get("/api/v2/attempts", params={**_RANGE, "limit": 1}, headers=auth)
    assert first.status_code == 200
    body = first.json()
    assert len(body["attempts"]) == 1
    assert body["attempts"][0]["event_id"] == "e2"  # newest first
    assert body["next_cursor"]

    second = client.get(
        "/api/v2/attempts", params={**_RANGE, "limit": 1, "cursor": body["next_cursor"]},
        headers=auth,
    )
    assert [a["event_id"] for a in second.json()["attempts"]] == ["e1"]


def test_attempts_reject_bad_cursor_and_range(client: TestClient, auth: dict[str, str]) -> None:
    bad = client.get("/api/v2/attempts", params={**_RANGE, "cursor": "!!"}, headers=auth)
    assert bad.status_code == 400
    wide = client.get(
        "/api/v2/attempts",
        params={"from": "2020-01-01T00:00:00Z", "to": "2026-01-01T00:00:00Z"},
        headers=auth,
    )
    assert wide.status_code == 400


def test_requests_listing_and_unknown_drilldown(client: TestClient, auth: dict[str, str]) -> None:
    response = client.get("/api/v2/requests", params=_RANGE, headers=auth)
    assert response.status_code == 200
    assert "requests" in response.json()
    missing = client.get("/api/v2/requests/anthropic/nope", headers=auth)
    assert missing.status_code == 404


def test_sessions_listing_and_detail(client: TestClient, auth: dict[str, str]) -> None:
    _ingest(client, auth, [_event("e1")])
    listed = client.get("/api/v2/sessions", params=_RANGE, headers=auth)
    assert listed.status_code == 200
    sessions = listed.json()["sessions"]
    assert any(s["session_id"] == "sess-1" for s in sessions)

    scoped_id = next(s["scoped_id"] for s in sessions if s["session_id"] == "sess-1")
    detail = client.get(f"/api/v2/sessions/{scoped_id}", headers=auth)
    assert detail.status_code == 200
    assert detail.json()["session_id"] == "sess-1"
    assert client.get("/api/v2/sessions/not-valid-b64!!", headers=auth).status_code == 400


def test_trace_requires_query_read_scope(client: TestClient, auth: dict[str, str]) -> None:
    token = client.post(
        "/api/v1/tokens", json={"label": "ingest", "scopes": [INGEST_EVENTS]}, headers=auth
    ).json()["token"]
    forbidden = client.get(
        "/api/v2/attempts", params=_RANGE, headers={"Authorization": f"Bearer {token}"}
    )
    assert forbidden.status_code == 403
