"""Realistic proxy-exporter fixture batches for the three providers (Task 65.4).

Each :class:`Scenario` is a named batch of v2 wire events (plain JSON dicts,
exactly as a proxy would POST them) paired with the ingest counts it should
produce on a fresh database. The batches deliberately exercise the identity and
finality rules an exporter must get right:

* provider-native token tiers (anthropic cache read plus short/long writes,
  openai cached input + reasoning tokens, zai GLM cached input),
* a streaming snapshot sequence that upgrades to a final revision,
* a logical request spanning multiple attempts (retry after a 429),
* cross-provider fallback with ``fallback_from`` / ``fallback_trigger`` set,
* a client-cancelled attempt with partial usage, and
* a failed attempt with zero tokens (FR-EVENT-024).

Keeping these as dicts (not model instances) makes them the portable shared
truth that the proxy repository re-uses (subtask 7); :func:`as_json` dumps them.
Counting rules (see ``test_ingest_v2.py``): the first time a ``(provider,
event_id)`` is seen it is ``accepted``; a higher-sequence or snapshot->final
revision of it is ``updated``; an identical final resubmission is ``duplicate``;
a same-sequence conflicting snapshot is ``rejected`` (the batch still commits).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# All fixtures share one deterministic, timezone-aware start timestamp; events
# that need ordering vary ``sequence`` and the completion timestamps instead.
_TS = "2026-07-10T12:00:00Z"
_TS_FIRST = "2026-07-10T12:00:01Z"
_TS_DONE = "2026-07-10T12:00:03Z"

# The exporter identifies itself as the AI-provider proxy gateway.
_SOURCE = {"type": "gateway", "name": "aiProviderProxy", "version": "1.4.0"}


def _event(event_id: str, provider: str, native_model: str, **over: Any) -> dict[str, Any]:
    """Build one wire event dict with the required envelope plus overrides."""
    event: dict[str, Any] = {
        "schema_version": 2,
        "event_id": event_id,
        "event_kind": "attempt",
        "finality": "final",
        "sequence": 1,
        "provider": provider,
        "native_model": native_model,
        "ts_started": _TS,
        "source": dict(_SOURCE),
    }
    event.update(over)
    return event


@dataclass(frozen=True)
class Scenario:
    """A named fixture batch and the ingest counts it yields on a fresh DB.

    ``rows`` is the number of distinct ``usage_events_v2`` rows the batch
    persists (revisions of one event collapse to a single row).
    """

    name: str
    provider: str
    events: list[dict[str, Any]]
    accepted: int
    rows: int
    updated: int = 0
    duplicate: int = 0
    rejected: int = 0


# --------------------------------------------------------------------------- #
# Anthropic
# --------------------------------------------------------------------------- #

_ANTHROPIC_CACHE = Scenario(
    name="anthropic_cache_read_and_writes",
    provider="anthropic",
    events=[
        _event(
            "anthropic:msg_cache_1",
            "anthropic",
            "claude-sonnet-4-5",
            requested_model="claude-sonnet-4-5",
            success=True,
            outcome="success",
            http_status=200,
            input_tokens=1200,
            output_tokens=350,
            cache_read_tokens=8000,
            cache_write_short_tokens=512,
            cache_write_long_tokens=2048,
            ts_first_token=_TS_FIRST,
            ts_completed=_TS_DONE,
            extra={"anthropic": {"stop_reason": "end_turn"}},
        )
    ],
    accepted=1,
    rows=1,
)

# A streaming response: snapshots at growing sequence, then the final revision.
# Same event_id throughout -> one row; first snapshot accepted, the rest update.
_ANTHROPIC_STREAM = Scenario(
    name="anthropic_streaming_snapshots",
    provider="anthropic",
    events=[
        _event(
            "anthropic:msg_stream_1",
            "anthropic",
            "claude-opus-4-6",
            finality="snapshot",
            sequence=1,
            output_tokens=40,
            ts_first_token=_TS_FIRST,
        ),
        _event(
            "anthropic:msg_stream_1",
            "anthropic",
            "claude-opus-4-6",
            finality="snapshot",
            sequence=2,
            output_tokens=180,
        ),
        _event(
            "anthropic:msg_stream_1",
            "anthropic",
            "claude-opus-4-6",
            finality="final",
            sequence=3,
            input_tokens=900,
            output_tokens=512,
            success=True,
            outcome="success",
            http_status=200,
            ts_completed=_TS_DONE,
        ),
    ],
    accepted=1,
    updated=2,
    rows=1,
)

# A logical request that 429s on the first attempt and succeeds on the retry.
# Two distinct attempt events (distinct event_ids) -> two rows, both accepted.
_ANTHROPIC_RETRY = Scenario(
    name="anthropic_retry_after_429",
    provider="anthropic",
    events=[
        _event(
            "anthropic:lr_retry#0",
            "anthropic",
            "claude-haiku-4-5",
            logical_request_id="lr_retry",
            attempt_id="att_0",
            finality="final",
            success=False,
            outcome="error",
            http_status=429,
            routing={"policy": "retry", "reason": "rate_limited", "attempt_index": 0},
        ),
        _event(
            "anthropic:lr_retry#1",
            "anthropic",
            "claude-haiku-4-5",
            logical_request_id="lr_retry",
            attempt_id="att_1",
            finality="final",
            success=True,
            outcome="success",
            http_status=200,
            input_tokens=600,
            output_tokens=220,
            routing={"policy": "retry", "attempt_index": 1},
            ts_completed=_TS_DONE,
        ),
    ],
    accepted=2,
    rows=2,
)

# A client that disconnected mid-stream: partial output, not a success.
_ANTHROPIC_CANCELLED = Scenario(
    name="anthropic_client_cancelled_partial",
    provider="anthropic",
    events=[
        _event(
            "anthropic:msg_cancel_1",
            "anthropic",
            "claude-sonnet-4-5",
            success=False,
            outcome="client_cancelled",
            input_tokens=1500,
            output_tokens=64,
            ts_first_token=_TS_FIRST,
        )
    ],
    accepted=1,
    rows=1,
)


# --------------------------------------------------------------------------- #
# OpenAI
# --------------------------------------------------------------------------- #

_OPENAI_CACHED_REASONING = Scenario(
    name="openai_cached_input_and_reasoning",
    provider="openai",
    events=[
        _event(
            "openai:resp_cache_1",
            "openai",
            "gpt-5",
            requested_model="gpt-5",
            success=True,
            outcome="success",
            http_status=200,
            input_tokens=2400,
            cache_read_tokens=1800,
            output_tokens=300,
            reasoning_tokens=1024,
            service_tier="priority",
            ts_first_token=_TS_FIRST,
            ts_completed=_TS_DONE,
        )
    ],
    accepted=1,
    rows=1,
)

_OPENAI_WEB_SEARCH = Scenario(
    name="openai_web_search_billable_unit",
    provider="openai",
    events=[
        _event(
            "openai:resp_search_1",
            "openai",
            "gpt-5",
            success=True,
            outcome="success",
            http_status=200,
            input_tokens=800,
            output_tokens=450,
            tool_call_count=2,
            billable_units={"web_search_request": 2},
            service_tier="standard",
            ts_completed=_TS_DONE,
        )
    ],
    accepted=1,
    rows=1,
)


# --------------------------------------------------------------------------- #
# Z.ai
# --------------------------------------------------------------------------- #

_ZAI_CACHED = Scenario(
    name="zai_glm_cached_input",
    provider="zai",
    events=[
        _event(
            "zai:chatcmpl_1",
            "zai",
            "glm-4.6",
            requested_model="glm-4.6",
            success=True,
            outcome="success",
            http_status=200,
            input_tokens=3000,
            cache_read_tokens=2600,
            output_tokens=280,
            ts_completed=_TS_DONE,
        )
    ],
    accepted=1,
    rows=1,
)


# --------------------------------------------------------------------------- #
# Cross-provider and edge cases
# --------------------------------------------------------------------------- #

# A logical request that fails on anthropic and falls back to openai. The two
# attempts are on different providers (distinct (provider, event_id) keys).
_FALLBACK = Scenario(
    name="cross_provider_fallback",
    provider="anthropic",
    events=[
        _event(
            "anthropic:lr_fb#0",
            "anthropic",
            "claude-opus-4-6",
            logical_request_id="lr_fb",
            attempt_id="fb_0",
            requested_model="auto",
            success=False,
            outcome="error",
            http_status=529,
            routing={"policy": "cascade", "reason": "overloaded", "attempt_index": 0},
        ),
        _event(
            "openai:lr_fb#1",
            "openai",
            "gpt-5",
            logical_request_id="lr_fb",
            attempt_id="fb_1",
            requested_model="auto",
            success=True,
            outcome="success",
            http_status=200,
            input_tokens=700,
            output_tokens=210,
            routing={
                "attempt_index": 1,
                "fallback_from": "claude-opus-4-6",
                "fallback_trigger": "overloaded",
            },
            ts_completed=_TS_DONE,
        ),
    ],
    accepted=2,
    rows=2,
)

# A hard failure with no usage at all -- must still be accepted (FR-EVENT-024).
_FAILED_ZERO = Scenario(
    name="failed_attempt_zero_tokens",
    provider="openai",
    events=[
        _event(
            "openai:resp_fail_1",
            "openai",
            "gpt-5",
            finality="final",
            success=False,
            outcome="error",
            http_status=500,
        )
    ],
    accepted=1,
    rows=1,
)


ALL_SCENARIOS: list[Scenario] = [
    _ANTHROPIC_CACHE,
    _ANTHROPIC_STREAM,
    _ANTHROPIC_RETRY,
    _ANTHROPIC_CANCELLED,
    _OPENAI_CACHED_REASONING,
    _OPENAI_WEB_SEARCH,
    _ZAI_CACHED,
    _FALLBACK,
    _FAILED_ZERO,
]


def all_events() -> list[dict[str, Any]]:
    """Every fixture event across all scenarios, in declaration order."""
    return [event for scenario in ALL_SCENARIOS for event in scenario.events]


def as_json(indent: int = 2) -> str:
    """Serialize every scenario to JSON (the form the proxy repo consumes)."""
    payload = [
        {
            "name": scenario.name,
            "provider": scenario.provider,
            "expected": {
                "accepted": scenario.accepted,
                "updated": scenario.updated,
                "duplicate": scenario.duplicate,
                "rejected": scenario.rejected,
                "rows": scenario.rows,
            },
            "events": scenario.events,
        }
        for scenario in ALL_SCENARIOS
    ]
    return json.dumps(payload, indent=indent, sort_keys=False)
