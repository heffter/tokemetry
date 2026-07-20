"""End-to-end acceptance suite proving the epic criteria on the full stack.

Drives the proxy fixtures through the real HTTP surface -- ingest, pricing, and
the Task 66 query endpoints -- and asserts the five acceptance groups (Task
65.5):

1. per-provider landing: usage counters and computed costs appear for
   anthropic, openai, and zai (AC-002);
2. fallback chains: every attempt counts once in usage totals and independently
   in attempt views, while the logical-request view reports attempt/fallback
   counts and the winning attempt without double counting (FR-TRACE-007,
   AC-005/006);
3. requested vs routed vs native model are distinct in attempt responses
   (US-005);
4. a streaming snapshot sequence resolves to exactly one final record (AC-004);
5. replaying a batch does not inflate totals (AC-003).

These run against the SQLite-backed HTTP client, the dialect every api_v2 HTTP
test uses (the async app and query services are wired to SQLite in tests). The
cross-dialect SQL these endpoints depend on is exercised on Postgres by the
sync migration/view suites when ``TOKEMETRY_TEST_POSTGRES_URL`` is set. Running
these same HTTP acceptance assertions on a Postgres-backed app is not yet wired
and is tracked as a high-priority follow-up (Task 77).
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from .fixtures import ALL_SCENARIOS, Scenario

# The fixture corpus keyed by name, so tests reuse the shared scenarios.
_SCENARIOS: dict[str, Scenario] = {scenario.name: scenario for scenario in ALL_SCENARIOS}

# All fixture events start on this day; queries bracket it generously.
_FROM = "2026-07-01T00:00:00Z"
_TO = "2026-08-01T00:00:00Z"

# Provider/model/unit rates seeded so computed costs are fully priced. Prices
# are representative, not authoritative; the suite asserts costs are non-zero
# and fully priced, not exact figures.
_RATES: list[tuple[str, str, str, str]] = [
    ("anthropic", "claude-sonnet-4-5", "input_token", "0.000003"),
    ("anthropic", "claude-sonnet-4-5", "output_token", "0.000015"),
    ("anthropic", "claude-sonnet-4-5", "cache_read_token", "0.0000003"),
    ("anthropic", "claude-sonnet-4-5", "cache_write_short_token", "0.00000375"),
    ("anthropic", "claude-sonnet-4-5", "cache_write_long_token", "0.000006"),
    ("openai", "gpt-5", "input_token", "0.0000025"),
    ("openai", "gpt-5", "output_token", "0.00001"),
    ("openai", "gpt-5", "cache_read_token", "0.00000025"),
    ("openai", "gpt-5", "reasoning_token", "0.00001"),
    ("zai", "glm-4.6", "input_token", "0.0000006"),
    ("zai", "glm-4.6", "output_token", "0.0000022"),
    ("zai", "glm-4.6", "cache_read_token", "0.00000011"),
]

_SOURCE = {"type": "gateway", "name": "aiProviderProxy", "version": "1.4.0"}


def _event(event_id: str, provider: str, native_model: str, **over: Any) -> dict[str, Any]:
    """Build a wire event dict (mirrors the fixtures builder for local events)."""
    event: dict[str, Any] = {
        "schema_version": 2,
        "event_id": event_id,
        "event_kind": "attempt",
        "finality": "final",
        "sequence": 1,
        "provider": provider,
        "native_model": native_model,
        "ts_started": "2026-07-10T12:00:00Z",
        "source": dict(_SOURCE),
    }
    event.update(over)
    return event


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #

def _ingest(
    client: TestClient, auth: dict[str, str], events: list[dict[str, Any]]
) -> dict[str, Any]:
    response = client.post(
        "/api/v2/ingest/events",
        json={"schema_version": 2, "events": events},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _usage(client: TestClient, auth: dict[str, str], group_by: str) -> dict[str, dict[str, Any]]:
    response = client.get(
        "/api/v2/usage",
        params={"from": _FROM, "to": _TO, "group_by": group_by},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    return {row["key"]: row for row in response.json()["rows"]}


def _costs(client: TestClient, auth: dict[str, str], group_by: str) -> dict[str, dict[str, Any]]:
    response = client.get(
        "/api/v2/costs",
        params={"from": _FROM, "to": _TO, "group_by": group_by},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    return {row["key"]: row for row in response.json()["rows"]}


def _attempts(
    client: TestClient, auth: dict[str, str], logical_request_id: str | None = None
) -> list[dict[str, Any]]:
    params: dict[str, str] = {"from": _FROM, "to": _TO}
    if logical_request_id is not None:
        params["logical_request_id"] = logical_request_id
    response = client.get("/api/v2/attempts", params=params, headers=auth)
    assert response.status_code == 200, response.text
    return list(response.json()["attempts"])


def _request_detail(
    client: TestClient, auth: dict[str, str], provider: str, logical_request_id: str
) -> dict[str, Any]:
    response = client.get(
        f"/api/v2/requests/{provider}/{logical_request_id}", headers=auth
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _seed_rates(client: TestClient, auth: dict[str, str]) -> None:
    for provider, native_model, unit_type, price in _RATES:
        response = client.post(
            "/api/v2/pricing",
            json={
                "provider": provider,
                "native_model": native_model,
                "unit_type": unit_type,
                "effective_from": "2026-01-01",
                "unit_price": price,
            },
            headers=auth,
        )
        assert response.status_code == 201, response.text


def _reprice(client: TestClient, auth: dict[str, str]) -> int:
    response = client.post(
        "/api/v2/pricing/reprice",
        json={"start": _FROM, "end": _TO},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    affected: int = response.json()["affected"]
    return affected


# --------------------------------------------------------------------------- #
# Group 1 -- per-provider landing and computed costs (AC-002)
# --------------------------------------------------------------------------- #

def test_all_three_providers_land_usage_and_costs(
    client: TestClient, auth: dict[str, str]
) -> None:
    """Usage counters and computed costs appear for every provider (AC-002)."""
    anthropic = _SCENARIOS["anthropic_cache_read_and_writes"].events[0]
    openai = _SCENARIOS["openai_cached_input_and_reasoning"].events[0]
    zai = _SCENARIOS["zai_glm_cached_input"].events[0]
    _ingest(client, auth, [anthropic, openai, zai])

    by_provider = _usage(client, auth, "provider")
    assert set(by_provider) == {"anthropic", "openai", "zai"}
    # Provider-native token tiers survive the round-trip.
    anthropic_row = by_provider["anthropic"]
    assert anthropic_row["cache_read_tokens"] == anthropic["cache_read_tokens"]
    assert anthropic_row["cache_write_long_tokens"] == anthropic["cache_write_long_tokens"]
    assert by_provider["openai"]["reasoning_tokens"] == openai["reasoning_tokens"]
    assert by_provider["zai"]["cache_read_tokens"] == zai["cache_read_tokens"]

    # Every native model is independently visible.
    by_model = _usage(client, auth, "model")
    assert {"claude-sonnet-4-5", "gpt-5", "glm-4.6"} <= set(by_model)

    # Costs compute and land, fully priced, for each provider.
    _seed_rates(client, auth)
    assert _reprice(client, auth) == 3
    costs = _costs(client, auth, "provider")
    assert set(costs) == {"anthropic", "openai", "zai"}
    for provider in ("anthropic", "openai", "zai"):
        assert float(costs[provider]["actual_spend_usd"]) > 0.0, provider
        assert costs[provider]["unpriced_event_count"] == 0, provider


# --------------------------------------------------------------------------- #
# Group 2 -- fallback chain: no double counting (FR-TRACE-007, AC-005/006)
# --------------------------------------------------------------------------- #

_LR = "lr_e2e_chain"
_FALLBACK_CHAIN = [
    _event(
        "anthropic:lr_e2e#0",
        "anthropic",
        "claude-opus-4-6",
        logical_request_id=_LR,
        attempt_id="a0",
        requested_model="auto",
        success=False,
        outcome="error",
        http_status=429,
        routing={"policy": "cascade", "reason": "rate_limited", "attempt_index": 0},
    ),
    _event(
        "anthropic:lr_e2e#1",
        "anthropic",
        "claude-sonnet-4-5",
        logical_request_id=_LR,
        attempt_id="a1",
        requested_model="auto",
        success=False,
        outcome="error",
        http_status=500,
        output_tokens=20,
        routing={
            "attempt_index": 1,
            "fallback_from": "claude-opus-4-6",
            "fallback_trigger": "rate_limited",
        },
    ),
    _event(
        "anthropic:lr_e2e#2",
        "anthropic",
        "claude-haiku-4-5",
        logical_request_id=_LR,
        attempt_id="a2",
        requested_model="auto",
        routed_model="claude-haiku-4-5-turbo",
        success=True,
        outcome="success",
        http_status=200,
        input_tokens=500,
        output_tokens=300,
        ts_completed="2026-07-10T12:00:04Z",
        routing={
            "attempt_index": 2,
            "fallback_from": "claude-sonnet-4-5",
            "fallback_trigger": "server_error",
        },
    ),
]
# Usage total = every attempt counted once: 0 + 20 + (500 + 300).
_CHAIN_TOTAL_TOKENS = 0 + 20 + 500 + 300


def test_fallback_chain_counts_each_attempt_once(
    client: TestClient, auth: dict[str, str]
) -> None:
    """Three attempts count independently in views, once in totals (AC-005/006)."""
    _ingest(client, auth, _FALLBACK_CHAIN)

    # Attempt view: one row per attempt.
    attempts = _attempts(client, auth, logical_request_id=_LR)
    assert {a["event_id"] for a in attempts} == {
        "anthropic:lr_e2e#0",
        "anthropic:lr_e2e#1",
        "anthropic:lr_e2e#2",
    }

    # Usage total: each attempt's tokens counted exactly once, no summary added.
    usage = _usage(client, auth, "provider")
    assert usage["anthropic"]["total_tokens"] == _CHAIN_TOTAL_TOKENS
    assert usage["anthropic"]["attempt_count"] == 3

    # Logical-request view: chain aggregates without inflating token totals.
    detail = _request_detail(client, auth, "anthropic", _LR)
    request = detail["request"]
    assert request["attempt_count"] == 3
    assert request["fallback_count"] == 2  # attempts 1 and 2 carry fallback_from
    # The winning attempt is identified by its attempt_id (the successful one).
    assert request["winning_attempt_id"] == "a2"
    assert request["total_tokens"] == _CHAIN_TOTAL_TOKENS
    assert len(detail["attempts"]) == 3


# --------------------------------------------------------------------------- #
# Group 3 -- model visibility: requested vs routed vs native (US-005)
# --------------------------------------------------------------------------- #

def test_requested_routed_native_models_are_distinct(
    client: TestClient, auth: dict[str, str]
) -> None:
    """An attempt exposes requested, routed, and native model distinctly."""
    event = _event(
        "anthropic:model_vis_1",
        "anthropic",
        "claude-haiku-4-5",
        requested_model="claude-auto",
        routed_model="claude-haiku-4-5-preview",
        success=True,
        outcome="success",
        input_tokens=100,
        output_tokens=50,
    )
    _ingest(client, auth, [event])

    (attempt,) = _attempts(client, auth)
    assert attempt["requested_model"] == "claude-auto"
    assert attempt["routed_model"] == "claude-haiku-4-5-preview"
    assert attempt["native_model"] == "claude-haiku-4-5"
    # All three are genuinely distinct values.
    assert len({attempt["requested_model"], attempt["routed_model"], attempt["native_model"]}) == 3


# --------------------------------------------------------------------------- #
# Group 4 -- streaming resolves to exactly one final record (AC-004)
# --------------------------------------------------------------------------- #

def test_streaming_snapshots_resolve_to_one_final_record(
    client: TestClient, auth: dict[str, str]
) -> None:
    """A snapshot sequence collapses to a single final usage record (AC-004)."""
    scenario = _SCENARIOS["anthropic_streaming_snapshots"]
    _ingest(client, auth, scenario.events)

    final = scenario.events[-1]
    expected_total = final["input_tokens"] + final["output_tokens"]

    attempts = _attempts(client, auth)
    assert len(attempts) == 1  # one record, not one per snapshot
    assert attempts[0]["event_id"] == "anthropic:msg_stream_1"
    assert attempts[0]["output_tokens"] == final["output_tokens"]

    usage = _usage(client, auth, "provider")
    # Totals reflect only the final revision, not the superseded snapshots.
    assert usage["anthropic"]["total_tokens"] == expected_total
    assert usage["anthropic"]["attempt_count"] == 1


# --------------------------------------------------------------------------- #
# Group 5 -- replay does not inflate totals (AC-003)
# --------------------------------------------------------------------------- #

def test_replay_does_not_inflate_totals(
    client: TestClient, auth: dict[str, str]
) -> None:
    """Re-ingesting a batch leaves usage totals and attempt counts unchanged."""
    events = _SCENARIOS["anthropic_cache_read_and_writes"].events
    _ingest(client, auth, events)
    before = _usage(client, auth, "provider")["anthropic"]

    replay = _ingest(client, auth, events)
    assert replay["accepted"] == 0
    assert replay["duplicate"] == len(events)

    after = _usage(client, auth, "provider")["anthropic"]
    assert after["total_tokens"] == before["total_tokens"]
    assert after["attempt_count"] == before["attempt_count"]
    assert len(_attempts(client, auth)) == 1
