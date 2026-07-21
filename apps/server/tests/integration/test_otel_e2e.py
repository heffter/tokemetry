"""End-to-end OTLP -> v2 -> dashboard visibility (Task 71.5).

Posts a realistic OTLP/HTTP JSON trace -- the kind an instrumented agent app
emits: a parent agent span with two child GenAI attempt spans (an errored
OpenAI call and a successful Anthropic fallback) -- and asserts the events land
and are visible through the query surface the dashboard consumes (usage, costs,
attempts, trace grouping).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from conftest import BOOTSTRAP_TOKEN
from fastapi.testclient import TestClient
from tokemetry_server.app import create_app
from tokemetry_server.config import Settings

_TRACES = "/api/v2/otel/v1/traces"
_RANGE = {"from": "2026-07-01T00:00:00Z", "to": "2026-08-01T00:00:00Z"}
_AUTH = {"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"}
_TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
_AGENT_SPAN = "00f067aa0ba902b7"
_OPENAI_SPAN = "b7ad6b7169203331"
_ANTHROPIC_SPAN = "a3ce929d0e0e4736"
_START = 1_783_684_800_000_000_000  # 2026-07-10T12:00:00Z


def _client(tmp_path: Path) -> TestClient:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'e2e.db'}",
        api_bootstrap_token=BOOTSTRAP_TOKEN,
        seed_default_alerts=False,
        cost_worker_enabled=False,
        otel_receiver_enabled=True,
    )
    return TestClient(create_app(settings=settings))


def _attr(key: str, **value: Any) -> dict[str, Any]:
    return {"key": key, "value": value}


def _span(
    span_id: str,
    parent: str | None,
    attributes: list[dict[str, Any]],
    *,
    name: str,
    offset_ns: int,
    duration_ns: int,
    status: str | None = None,
) -> dict[str, Any]:
    span: dict[str, Any] = {
        "traceId": _TRACE,
        "spanId": span_id,
        "name": name,
        "startTimeUnixNano": str(_START + offset_ns),
        "endTimeUnixNano": str(_START + offset_ns + duration_ns),
        "attributes": attributes,
    }
    if parent is not None:
        span["parentSpanId"] = parent
    if status is not None:
        span["status"] = {"code": status}
    return span


def _instrumented_trace() -> dict[str, Any]:
    """One agent turn: an OpenAI attempt fails, an Anthropic fallback succeeds."""
    agent = _span(
        _AGENT_SPAN,
        None,
        [_attr("gen_ai.operation.name", stringValue="invoke_agent")],
        name="agent turn",
        offset_ns=0,
        duration_ns=3_000_000_000,
    )
    openai = _span(
        _OPENAI_SPAN,
        _AGENT_SPAN,
        [
            _attr("gen_ai.system", stringValue="openai"),
            _attr("gen_ai.request.model", stringValue="gpt-5"),
            _attr("error.type", stringValue="RateLimitError"),
            _attr("gen_ai.prompt", stringValue="SECRET user prompt"),
        ],
        name="chat gpt-5",
        offset_ns=100_000_000,
        duration_ns=500_000_000,
        status="STATUS_CODE_ERROR",
    )
    anthropic = _span(
        _ANTHROPIC_SPAN,
        _AGENT_SPAN,
        [
            _attr("gen_ai.system", stringValue="anthropic"),
            _attr("gen_ai.request.model", stringValue="claude-auto"),
            _attr("gen_ai.response.model", stringValue="claude-sonnet-4-5"),
            _attr("gen_ai.usage.input_tokens", intValue="1200"),
            _attr("gen_ai.usage.output_tokens", intValue="400"),
            _attr("gen_ai.completion", stringValue="SECRET assistant reply"),
        ],
        name="chat claude",
        offset_ns=700_000_000,
        duration_ns=1_800_000_000,
        status="STATUS_CODE_OK",
    )
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [_attr("service.name", stringValue="my-agent")]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "opentelemetry.instrumentation.openai"},
                        "spans": [agent, openai, anthropic],
                    }
                ],
            }
        ]
    }


def test_instrumented_trace_lands_and_is_dashboard_visible(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post(_TRACES, json=_instrumented_trace(), headers=_AUTH)
        assert response.status_code == 200, response.text
        # Two GenAI attempts land; the parent agent span is not a GenAI span.
        assert response.json()["accepted"] == 2

        # Attempts view: both provider calls, grouped by the shared trace.
        attempts = client.get(
            "/api/v2/attempts", params={**_RANGE, "trace_id": _TRACE}, headers=_AUTH
        ).json()["attempts"]
        by_provider = {a["provider"]: a for a in attempts}
        assert set(by_provider) == {"openai", "anthropic"}
        assert by_provider["openai"]["success"] is False
        assert by_provider["anthropic"]["success"] is True
        assert by_provider["anthropic"]["native_model"] == "claude-sonnet-4-5"
        # Content never survived.
        import json

        assert "SECRET" not in json.dumps(attempts)

        # Usage view (dashboard): per-provider token counts.
        usage = client.get(
            "/api/v2/usage",
            params={**_RANGE, "group_by": "provider"},
            headers=_AUTH,
        ).json()["rows"]
        anthropic_usage = next(r for r in usage if r["key"] == "anthropic")
        assert anthropic_usage["total_tokens"] == 1600  # 1200 + 400

        # Costs view (dashboard): price the model, reprice, and see spend.
        for unit, price in (("input_token", "0.000003"), ("output_token", "0.000015")):
            assert (
                client.post(
                    "/api/v2/pricing",
                    json={
                        "provider": "anthropic",
                        "native_model": "claude-sonnet-4-5",
                        "unit_type": unit,
                        "effective_from": "2026-01-01",
                        "unit_price": price,
                    },
                    headers=_AUTH,
                ).status_code
                == 201
            )
        assert (
            client.post(
                "/api/v2/pricing/reprice",
                json={"start": _RANGE["from"], "end": _RANGE["to"]},
                headers=_AUTH,
            ).status_code
            == 200
        )
        costs = client.get(
            "/api/v2/costs", params={**_RANGE, "group_by": "provider"}, headers=_AUTH
        ).json()["rows"]
        anthropic_cost = next(r for r in costs if r["key"] == "anthropic")
        assert float(anthropic_cost["actual_spend_usd"]) > 0.0
