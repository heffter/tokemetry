"""v1<->v2 query accounting consistency (Task 66.9, FR-QUERY-008/012).

The v1 golden suite already proves the ``/api/v1`` query responses are byte
unchanged by the v2 work. This adds the differential check: the same underlying
data, queried through v1 and v2, agrees on usage totals -- so there is no double
counting across the two API generations -- and the v2 attempt count matches the
events ingested (snapshots and logical-request summaries excluded).
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

_MACHINE = {"name": "box-1", "collector_version": "1.0.0"}
_V1_RANGE = {"from": "2026-01-01", "to": "2026-12-31"}
_V2_RANGE = {"from": "2026-01-01T00:00:00Z", "to": "2026-12-31T00:00:00Z"}


def _v1_event(event_id: str, **overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event_id": event_id,
        "provider": "anthropic",
        "native_model": "claude-opus-4-5",
        "ts": "2026-07-09T09:41:14+00:00",
        "project": "proj-a",
        "input_tokens": 1_000_000,
        "output_tokens": 500,
        "cache_read_tokens": 0,
        "cache_write_short_tokens": 0,
        "cache_write_long_tokens": 0,
    }
    event.update(overrides)
    return event


def _ingest_v1(client: TestClient, auth: dict[str, str], *events: dict[str, Any]) -> None:
    response = client.post(
        "/api/v1/ingest/events",
        json={"machine": _MACHINE, "events": list(events)},
        headers=auth,
    )
    assert response.status_code == 200


def test_v1_and_v2_usage_totals_agree(client: TestClient, auth: dict[str, str]) -> None:
    _ingest_v1(client, auth, _v1_event("req_1"), _v1_event("req_2"))

    v1 = client.get(
        "/api/v1/usage", params={**_V1_RANGE, "group_by": "provider"}, headers=auth
    ).json()
    v2 = client.get(
        "/api/v2/usage", params={**_V2_RANGE, "group_by": "provider"}, headers=auth
    ).json()

    v1_total = sum(bucket["total_tokens"] for bucket in v1["buckets"])
    v2_total = sum(row["total_tokens"] for row in v2["rows"])
    # The v1 rollup path and the v2 ledger path agree on the same underlying data.
    assert v1_total == v2_total == 2 * (1_000_000 + 500)


def test_v2_does_not_double_count_attempts(client: TestClient, auth: dict[str, str]) -> None:
    _ingest_v1(client, auth, _v1_event("req_1"), _v1_event("req_2"), _v1_event("req_3"))
    v2 = client.get(
        "/api/v2/usage", params={**_V2_RANGE, "group_by": "provider"}, headers=auth
    ).json()
    # Exactly one attempt per ingested event; no snapshot or logical-request inflation.
    assert sum(row["attempt_count"] for row in v2["rows"]) == 3


def test_v1_usage_endpoint_still_serves_its_shape(
    client: TestClient, auth: dict[str, str]
) -> None:
    _ingest_v1(client, auth, _v1_event("req_1"))
    v1 = client.get(
        "/api/v1/usage", params={**_V1_RANGE, "group_by": "model"}, headers=auth
    )
    assert v1.status_code == 200
    assert "buckets" in v1.json()  # the unchanged v1 contract
