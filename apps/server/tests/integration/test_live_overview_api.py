"""Live-overview endpoint wiring and filter passing (Task 73, endpoint level)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

_URL = "/api/v2/summary/live-overview"


def _event(event_id: str, provider: str, model: str, ts: str, output: int) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "event_id": event_id,
        "event_kind": "attempt",
        "finality": "final",
        "sequence": 1,
        "provider": provider,
        "native_model": model,
        "ts_started": ts,
        "output_tokens": output,
        "source": {"type": "gateway", "name": "proxy", "version": "1"},
    }


def _seed(client: TestClient, auth: dict[str, str]) -> None:
    # Ingest at the current time so events fall inside the live burn window.
    ts = datetime.now(UTC).isoformat()
    response = client.post(
        "/api/v2/ingest/events",
        json={
            "schema_version": 2,
            "events": [
                _event("anthropic:a1", "anthropic", "claude-sonnet-4-5", ts, 600),
                _event("anthropic:a2", "anthropic", "claude-haiku-4-5", ts, 300),
                _event("openai:o1", "openai", "gpt-5", ts, 100),
            ],
        },
        headers=auth,
    )
    assert response.status_code == 200, response.text


def test_live_overview_shape_and_burn(client: TestClient, auth: dict[str, str]) -> None:
    _seed(client, auth)
    body = client.get(_URL, headers=auth).json()
    assert body["burn_rate_per_min"] > 0
    assert isinstance(body["provider_limits"], list)
    models = {m["native_model"]: m["total_tokens"] for m in body["today_by_model"]}
    assert models["claude-sonnet-4-5"] == 600
    assert models["gpt-5"] == 100


def test_live_overview_honors_provider_filter(
    client: TestClient, auth: dict[str, str]
) -> None:
    _seed(client, auth)
    body = client.get(_URL, params={"provider": "anthropic"}, headers=auth).json()
    models = {m["native_model"] for m in body["today_by_model"]}
    assert models == {"claude-sonnet-4-5", "claude-haiku-4-5"}  # no gpt-5


def test_live_overview_requires_auth(client: TestClient) -> None:
    assert client.get(_URL).status_code == 401
