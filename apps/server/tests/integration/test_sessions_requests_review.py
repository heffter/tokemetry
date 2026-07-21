"""Sessions project attribution and single-attempt request visibility.

Covers the review-driven changes: a session surfaces its dominant project and
is filterable by project, and an event with no explicit ``logical_request_id``
(e.g. a Claude Code transcript row) still shows up as a single-attempt request
instead of being invisible on the requests surface.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

_RANGE = {"from": "2026-07-01T00:00:00Z", "to": "2026-08-01T00:00:00Z"}


def _event(event_id: str, **over: Any) -> dict[str, Any]:
    event = {
        "schema_version": 2,
        "event_id": event_id,
        "event_kind": "attempt",
        "finality": "final",
        "sequence": 1,
        "provider": "anthropic",
        "native_model": "claude-sonnet-4-5",
        "ts_started": "2026-07-10T12:00:00Z",
        "output_tokens": 10,
        "source": {"type": "collector", "name": "box", "version": "1"},
    }
    event.update(over)
    return event


def _ingest(
    client: TestClient, auth: dict[str, str], events: list[dict[str, Any]]
) -> None:
    response = client.post(
        "/api/v2/ingest/events",
        json={"schema_version": 2, "events": events},
        headers=auth,
    )
    assert response.status_code == 200, response.text


def test_event_without_logical_request_id_appears_as_request(
    client: TestClient, auth: dict[str, str]
) -> None:
    """A null logical-request id defaults to the event id, so it is a request."""
    _ingest(client, auth, [_event("anthropic:solo")])

    response = client.get("/api/v2/requests", params=_RANGE, headers=auth)
    assert response.status_code == 200, response.text
    requests = response.json()["requests"]
    by_id = {r["logical_request_id"]: r for r in requests}
    assert "anthropic:solo" in by_id
    solo = by_id["anthropic:solo"]
    assert solo["attempt_count"] == 1
    assert solo["fallback_count"] == 0


def test_session_primary_project_and_project_filter(
    client: TestClient, auth: dict[str, str]
) -> None:
    """Sessions surface their dominant project and honor the project filter."""
    _ingest(
        client,
        auth,
        [
            _event("anthropic:s1a", session_id="sess-1", project="proj-a", output_tokens=10),
            _event("anthropic:s1b", session_id="sess-1", project="proj-b", output_tokens=100),
            _event("anthropic:s2", session_id="sess-2", project="proj-a", output_tokens=5),
        ],
    )

    response = client.get("/api/v2/sessions", params=_RANGE, headers=auth)
    assert response.status_code == 200, response.text
    sessions = {s["session_id"]: s for s in response.json()["sessions"]}
    # sess-1 spans two projects; the one with more tokens wins.
    assert sessions["sess-1"]["primary_project"] == "proj-b"
    assert sessions["sess-2"]["primary_project"] == "proj-a"

    # The project filter selects sessions that touched the project at all.
    filtered = client.get(
        "/api/v2/sessions", params={**_RANGE, "project": "proj-b"}, headers=auth
    ).json()["sessions"]
    assert {s["session_id"] for s in filtered} == {"sess-1"}
