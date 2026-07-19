"""CSV export round-trip and header stability for v2 query endpoints (Task 66.7)."""

from __future__ import annotations

import csv
import io
from typing import Any

from fastapi.testclient import TestClient

_RANGE = {"from": "2026-07-01T00:00:00Z", "to": "2026-08-01T00:00:00Z"}

_USAGE_HEADER = [
    "key", "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_write_short_tokens", "cache_write_long_tokens", "reasoning_tokens",
    "total_tokens", "attempt_count",
]
_COST_HEADER = [
    "key", "actual_spend_usd", "subscription_value_usd", "cost_priced_usd",
    "cost_partial_usd", "cost_estimated_usd", "unpriced_event_count", "pricing_version",
]


def _event(
    event_id: str, provider: str = "anthropic", model: str = "claude-sonnet-4-5"
) -> dict[str, Any]:
    return {
        "schema_version": 2, "event_id": event_id, "event_kind": "attempt",
        "finality": "final", "sequence": 1, "provider": provider,
        "native_model": model, "ts_started": "2026-07-10T12:00:00Z",
        "input_tokens": 1000,
        "source": {"type": "gateway", "name": "proxy", "version": "1.0"},
    }


def _ingest(client: TestClient, auth: dict[str, str], events: list[dict[str, Any]]) -> None:
    r = client.post(
        "/api/v2/ingest/events", json={"schema_version": 2, "events": events}, headers=auth
    )
    assert r.status_code == 200


def _csv_rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


def test_usage_csv_matches_json(client: TestClient, auth: dict[str, str]) -> None:
    _ingest(client, auth, [_event("a:1"), _event("o:1", provider="openai", model="gpt-5")])
    params = {**_RANGE, "group_by": "provider"}
    csv_resp = client.get("/api/v2/usage", params={**params, "format": "csv"}, headers=auth)
    assert csv_resp.status_code == 200
    assert csv_resp.headers["content-type"].startswith("text/csv")

    rows = _csv_rows(csv_resp.text)
    assert rows[0] == _USAGE_HEADER  # stable header (FR-QUERY-009)

    json_rows = client.get("/api/v2/usage", params=params, headers=auth).json()["rows"]
    # Every CSV data row round-trips to the matching JSON row.
    by_key = {r["key"]: r for r in json_rows}
    assert len(rows) - 1 == len(json_rows)
    for row in rows[1:]:
        record = dict(zip(_USAGE_HEADER, row, strict=True))
        assert int(record["input_tokens"]) == by_key[record["key"]]["input_tokens"]
        assert int(record["attempt_count"]) == by_key[record["key"]]["attempt_count"]


def test_costs_csv_header_is_stable(client: TestClient, auth: dict[str, str]) -> None:
    resp = client.get(
        "/api/v2/costs", params={**_RANGE, "group_by": "provider", "format": "csv"}, headers=auth
    )
    assert resp.status_code == 200
    assert _csv_rows(resp.text)[0] == _COST_HEADER


def test_attempts_and_rollups_csv_stream(client: TestClient, auth: dict[str, str]) -> None:
    _ingest(client, auth, [_event("a:1")])
    attempts = client.get("/api/v2/attempts", params={**_RANGE, "format": "csv"}, headers=auth)
    assert attempts.status_code == 200
    header = _csv_rows(attempts.text)[0]
    assert header[0] == "event_id" and "cost_usd" in header

    rollups = client.get(
        "/api/v2/rollups", params={"from": "2026-07-01", "to": "2026-07-31", "format": "csv"},
        headers=auth,
    )
    assert rollups.status_code == 200
    assert _csv_rows(rollups.text)[0][0] == "id"  # RollupOut field order
