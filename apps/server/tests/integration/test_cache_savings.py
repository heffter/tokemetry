"""Server-side cache-savings computation (Task 74, Gap 2)."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

_URL = "/api/v2/summary/cache-savings"
_RANGE = {"from": "2026-07-01", "to": "2026-07-31"}


def _event(event_id: str, provider: str, cache_read: int) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "event_id": event_id,
        "event_kind": "attempt",
        "finality": "final",
        "sequence": 1,
        "provider": provider,
        "native_model": "claude-sonnet-4-5",
        "ts_started": "2026-07-10T12:00:00Z",
        "input_tokens": 100,
        "cache_read_tokens": cache_read,
        "source": {"type": "gateway", "name": "proxy", "version": "1"},
    }


def _ingest(client: TestClient, auth: dict[str, str], events: list[dict[str, Any]]) -> None:
    response = client.post(
        "/api/v2/ingest/events",
        json={"schema_version": 2, "events": events},
        headers=auth,
    )
    assert response.status_code == 200, response.text


def _price(
    client: TestClient, auth: dict[str, str], unit: str, price: str
) -> None:
    response = client.post(
        "/api/v2/pricing",
        json={
            "provider": "anthropic",
            "native_model": "claude-sonnet-4-5",
            "unit_type": unit,
            "effective_from": "2026-01-01",
            "unit_price": price,
        },
        headers=auth,
    )
    assert response.status_code == 201, response.text


def _savings(client: TestClient, auth: dict[str, str], **extra: str) -> float:
    body = client.get(_URL, params={**_RANGE, **extra}, headers=auth).json()
    return float(body["cache_savings_usd"])


def test_cache_savings_computed_from_rates(
    client: TestClient, auth: dict[str, str]
) -> None:
    _ingest(client, auth, [_event("a1", "anthropic", 1000)])
    _price(client, auth, "input_token", "0.000003")
    _price(client, auth, "cache_read_token", "0.0000003")
    # 1000 * (0.000003 - 0.0000003) = 0.0027
    assert abs(_savings(client, auth) - 0.0027) < 1e-9


def test_unpriced_yields_zero_savings(
    client: TestClient, auth: dict[str, str]
) -> None:
    _ingest(client, auth, [_event("a1", "anthropic", 1000)])
    # No rate cards -> no authoritative saving to claim.
    assert _savings(client, auth) == 0.0


def test_cache_savings_honors_filter(
    client: TestClient, auth: dict[str, str]
) -> None:
    _ingest(client, auth, [_event("a1", "anthropic", 1000)])
    _price(client, auth, "input_token", "0.000003")
    _price(client, auth, "cache_read_token", "0.0000003")
    # Filtering to a provider with no cache reads yields zero.
    assert _savings(client, auth, provider="openai") == 0.0


def test_cache_savings_requires_auth(client: TestClient) -> None:
    assert client.get(_URL).status_code == 401
