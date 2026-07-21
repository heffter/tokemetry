"""Prohibited content, oversized, and over-deep payloads (Task 70.8, PRD 18.6).

Every metadata extension point rejects content-bearing keys, and both ingest
endpoints bound request size and JSON depth (NFR-SEC-004, FR-PRIV-012).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from conftest import BOOTSTRAP_TOKEN
from fastapi.testclient import TestClient
from tokemetry_server.app import create_app
from tokemetry_server.config import Settings

_INGEST = "/api/v2/ingest/events"
_AUTH = {"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"}


def _event(event_id: str = "anthropic:c1", **over: Any) -> dict[str, Any]:
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
        "source": {"type": "gateway", "name": "proxy", "version": "1"},
    }
    event.update(over)
    return event


def _ingest(client: TestClient, event: dict[str, Any]) -> Any:
    return client.post(
        _INGEST, json={"schema_version": 2, "events": [event]}, headers=_AUTH
    )


def test_prohibited_key_in_extra_rejected(client: TestClient) -> None:
    for key in ("prompt", "response_text", "message_body", "code_snippet"):
        response = _ingest(client, _event(extra={key: "secret content"}))
        assert response.status_code == 422, f"{key} should be rejected"


def test_prohibited_key_in_dimensions_rejected(client: TestClient) -> None:
    response = _ingest(client, _event(dimensions={"prompt": "leak"}))
    assert response.status_code == 422


def test_deeply_nested_extra_rejected(client: TestClient) -> None:
    nested: dict[str, Any] = {"v": 1}
    for _ in range(12):  # exceeds the default max depth of 8
        nested = {"n": nested}
    response = _ingest(client, _event(extra=nested))
    assert response.status_code == 422


def test_oversized_request_rejected(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'sec.db'}",
        api_bootstrap_token=BOOTSTRAP_TOKEN,
        seed_default_alerts=False,
        cost_worker_enabled=False,
        max_request_bytes=300,
    )
    with TestClient(create_app(settings=settings)) as client:
        big = {
            "schema_version": 2,
            "events": [_event(f"anthropic:big{i}") for i in range(50)],
        }
        assert client.post(_INGEST, json=big, headers=_AUTH).status_code == 413
