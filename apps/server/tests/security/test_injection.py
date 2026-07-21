"""SQL and filter injection attempts through query parameters (Task 70.8, PRD 18.6).

Every query filter is parameterized, so an injection string is treated as a
literal value (it simply matches nothing) and never alters or drops data.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

_RANGE = {"from": "2026-07-01T00:00:00Z", "to": "2026-08-01T00:00:00Z"}
_USAGE = "/api/v2/usage"

_INJECTIONS = [
    "'; DROP TABLE usage_events_v2; --",
    "' OR '1'='1",
    "box-a' UNION SELECT token_hash FROM api_tokens --",
    "%",  # SQL LIKE wildcard must not be treated as a pattern
]


def _event() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "event_id": "anthropic:inj1",
        "event_kind": "attempt",
        "finality": "final",
        "sequence": 1,
        "provider": "anthropic",
        "native_model": "claude-sonnet-4-5",
        "ts_started": "2026-07-10T12:00:00Z",
        "machine": "box-a",
        "output_tokens": 10,
        "source": {"type": "gateway", "name": "proxy", "version": "1"},
    }


def _rows(client: TestClient, auth: dict[str, str], **params: str) -> list[dict[str, Any]]:
    response = client.get(
        _USAGE, params={**_RANGE, "group_by": "machine", **params}, headers=auth
    )
    assert response.status_code == 200, response.text
    return list(response.json()["rows"])


def test_injection_in_filter_matches_nothing_and_preserves_data(
    client: TestClient, auth: dict[str, str]
) -> None:
    client.post(
        "/api/v2/ingest/events",
        json={"schema_version": 2, "events": [_event()]},
        headers=auth,
    )
    # A legitimate query still finds the seeded row.
    assert any(r["key"] == "box-a" for r in _rows(client, auth))

    for payload in _INJECTIONS:
        rows = _rows(client, auth, machine=payload)
        assert rows == [], f"injection leaked rows: {payload!r}"

    # The table was not dropped and no data leaked: the legitimate query still
    # works after every injection attempt.
    assert any(r["key"] == "box-a" for r in _rows(client, auth))


def test_injection_in_group_by_is_rejected_not_executed(
    client: TestClient, auth: dict[str, str]
) -> None:
    """An injection in group_by fails validation (400), never reaches SQL."""
    response = client.get(
        _USAGE,
        params={**_RANGE, "group_by": "machine; DROP TABLE usage_events_v2"},
        headers=auth,
    )
    assert response.status_code == 400
