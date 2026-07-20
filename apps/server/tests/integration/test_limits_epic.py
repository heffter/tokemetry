"""End-to-end limits epic acceptance (Task 69.7, PRD Epic TOK-12).

Seeds collector-official streams for anthropic, openai, and zai plus a
gateway-estimated stream, then asserts the v2 limits API returns every stream
with the right registry labels, provenance, dimensions, and per-stream
forecasts, and that the gateway and collector streams stay distinct.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

_RANGE = {"from": "2026-07-01T00:00:00Z", "to": "2026-08-01T00:00:00Z"}


def _snap(
    provider: str,
    window_kind: str,
    minutes: int,
    util: float,
    *,
    source_type: str,
    source_name: str,
    provenance: str = "official",
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "provider": provider,
        "window_kind": window_kind,
        "ts": f"2026-07-10T12:{minutes:02d}:00Z",
        "utilization_pct": util,
        "provenance": provenance,
        "source": {"type": source_type, "name": source_name, "version": "1.0"},
    }


def _stream(
    provider: str, window_kind: str, *, source_type: str, source_name: str, prov: str = "official"
) -> list[dict[str, Any]]:
    # Two rising readings 40 minutes apart, so a forecast is computable.
    return [
        _snap(
            provider, window_kind, 0, 20.0,
            source_type=source_type, source_name=source_name, provenance=prov,
        ),
        _snap(
            provider, window_kind, 40, 60.0,
            source_type=source_type, source_name=source_name, provenance=prov,
        ),
    ]


def _ingest(client: TestClient, auth: dict[str, str], snapshots: list[dict[str, Any]]) -> None:
    body = {"schema_version": 2, "snapshots": snapshots}
    response = client.post("/api/v2/ingest/limits", json=body, headers=auth)
    assert response.status_code == 200, response.text


def test_all_provider_and_gateway_streams_flow_through_v2(
    client: TestClient, auth: dict[str, str]
) -> None:
    _ingest(
        client,
        auth,
        [
            *_stream("anthropic", "five_hour", source_type="collector", source_name="col-a"),
            *_stream("openai", "primary", source_type="collector", source_name="col-o"),
            *_stream("zai", "prompt_5h", source_type="collector", source_name="col-z"),
            # A gateway-estimated stream for the same anthropic provider.
            *_stream(
                "anthropic", "requests_per_minute",
                source_type="gateway", source_name="gw-1", prov="local_estimate",
            ),
        ],
    )

    # Registry labels are exposed for every seeded window kind (FR-LIMIT-012).
    providers = {p["id"]: p for p in client.get("/api/v2/providers", headers=auth).json()}
    labels = {w["kind"]: w["label"] for w in providers["anthropic"]["windows"]}
    assert labels["five_hour"] == "5-hour block"
    assert labels["requests_per_minute"] == "Requests / min"
    assert {w["kind"] for w in providers["openai"]["windows"]} >= {"primary"}
    assert {w["kind"] for w in providers["zai"]["windows"]} == {"prompt_5h"}

    # Every stream's snapshots are queryable with their provenance.
    limits = client.get("/api/v2/limits", params=_RANGE, headers=auth).json()["limits"]
    provenances: dict[str, set[str]] = {row["provider"]: set() for row in limits}
    for row in limits:
        provenances[row["provider"]].add(row["provenance"])
    assert {"anthropic", "openai", "zai"} <= set(provenances)
    # Anthropic carries both a collector-official and a gateway-estimated reading.
    assert provenances["anthropic"] == {"official", "local_estimate"}

    # Forecasts: one per stream, gateway and collector never merged (FR-LIMIT-005).
    forecasts = client.get(
        "/api/v2/limits/forecast", params=_RANGE, headers=auth
    ).json()["forecasts"]
    streams = {(f["stream"]["provider"], f["stream"]["window_kind"]) for f in forecasts}
    assert ("anthropic", "five_hour") in streams
    assert ("anthropic", "requests_per_minute") in streams  # gateway stream distinct
    assert ("openai", "primary") in streams
    assert ("zai", "prompt_5h") in streams
    # The rising streams produce a real (non-unavailable) forecast.
    anthropic_5h = next(
        f for f in forecasts
        if f["stream"]["provider"] == "anthropic" and f["stream"]["window_kind"] == "five_hour"
    )
    assert anthropic_5h["confidence"] != "unavailable"
    assert anthropic_5h["predicted_exhaustion_at"] is not None
    # The two anthropic streams resolved to distinct sources (no-merge).
    anthropic_streams = {
        f["stream"]["source_id"]
        for f in forecasts
        if f["stream"]["provider"] == "anthropic"
    }
    assert len(anthropic_streams) == 2
