"""Feature-flagged OTLP/HTTP trace receiver (Task 71.3, FR-OTEL-004/007)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from conftest import BOOTSTRAP_TOKEN
from fastapi.testclient import TestClient
from tokemetry_server.app import create_app
from tokemetry_server.config import Settings
from tokemetry_server.scopes import QUERY_READ

_TRACES = "/api/v2/otel/v1/traces"
_RANGE = {"from": "2026-07-01T00:00:00Z", "to": "2026-08-01T00:00:00Z"}
_AUTH = {"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"}
_TRACE = "0af7651916cd43dd8448eb211c80319c"
_SPAN = "b7ad6b7169203331"


def _build(tmp_path: Path, **overrides: Any) -> TestClient:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'otel.db'}",
        api_bootstrap_token=BOOTSTRAP_TOKEN,
        seed_default_alerts=False,
        cost_worker_enabled=False,
        **overrides,
    )
    return TestClient(create_app(settings=settings))


def _attr(key: str, **value: Any) -> dict[str, Any]:
    return {"key": key, "value": value}


def _otlp(
    attributes: list[dict[str, Any]],
    *,
    span_id: str = _SPAN,
    status: str | None = None,
) -> dict[str, Any]:
    span: dict[str, Any] = {
        "traceId": _TRACE,
        "spanId": span_id,
        "name": "chat",
        # 2026-07-10T12:00:00Z, inside the query window; end is +2s.
        "startTimeUnixNano": "1783684800000000000",
        "endTimeUnixNano": "1783684802000000000",
        "attributes": attributes,
    }
    if status is not None:
        span["status"] = {"code": status}
    return {"resourceSpans": [{"scopeSpans": [{"scope": {"name": "app"}, "spans": [span]}]}]}


def _genai_span() -> dict[str, Any]:
    return _otlp(
        [
            _attr("gen_ai.system", stringValue="OpenAI"),
            _attr("gen_ai.request.model", stringValue="gpt-5-preview"),
            _attr("gen_ai.response.model", stringValue="gpt-5"),
            _attr("gen_ai.usage.input_tokens", intValue="1000"),
            _attr("gen_ai.usage.output_tokens", intValue="300"),
            _attr("gen_ai.prompt", stringValue="secret prompt text"),
        ]
    )


def test_receiver_absent_by_default(tmp_path: Path) -> None:
    with _build(tmp_path) as client:
        response = client.post(_TRACES, json=_genai_span(), headers=_AUTH)
        assert response.status_code == 404  # router not mounted


def test_receiver_ingests_genai_span(tmp_path: Path) -> None:
    with _build(tmp_path, otel_receiver_enabled=True) as client:
        response = client.post(_TRACES, json=_genai_span(), headers=_AUTH)
        assert response.status_code == 200, response.text
        assert response.json()["accepted"] == 1

        attempts = client.get(
            "/api/v2/attempts", params=_RANGE, headers=_AUTH
        ).json()["attempts"]
        (attempt,) = attempts
        assert attempt["provider"] == "openai"  # normalized from "OpenAI"
        assert attempt["native_model"] == "gpt-5"
        assert attempt["requested_model"] == "gpt-5-preview"
        assert attempt["input_tokens"] == 1000
        assert attempt["output_tokens"] == 300
        assert attempt["trace_id"] == _TRACE
        assert attempt["latency_ms"] == 2000


def test_content_attributes_are_stripped(tmp_path: Path) -> None:
    with _build(tmp_path, otel_receiver_enabled=True) as client:
        client.post(_TRACES, json=_genai_span(), headers=_AUTH)
        # The prompt is never stored: it appears nowhere in the attempt record.
        import json

        attempts = client.get(
            "/api/v2/attempts", params=_RANGE, headers=_AUTH
        ).json()
        assert "secret prompt text" not in json.dumps(attempts)


def test_non_genai_spans_are_ignored(tmp_path: Path) -> None:
    with _build(tmp_path, otel_receiver_enabled=True) as client:
        payload = _otlp([_attr("http.method", stringValue="GET")])
        response = client.post(_TRACES, json=payload, headers=_AUTH)
        assert response.status_code == 200
        assert response.json()["accepted"] == 0  # not a GenAI span


def test_receiver_requires_ingest_scope(tmp_path: Path) -> None:
    with _build(tmp_path, otel_receiver_enabled=True) as client:
        # Mint a query-only token; it cannot post traces.
        minted = client.post(
            "/api/v1/tokens",
            json={"label": "reader", "scopes": [QUERY_READ]},
            headers=_AUTH,
        ).json()["token"]
        response = client.post(
            _TRACES, json=_genai_span(), headers={"Authorization": f"Bearer {minted}"}
        )
        assert response.status_code == 403


def test_malformed_payload_rejected(tmp_path: Path) -> None:
    with _build(tmp_path, otel_receiver_enabled=True) as client:
        response = client.post(_TRACES, json={"not": "otlp"}, headers=_AUTH)
        assert response.status_code == 400
