"""Unit tests for the Python ingest client (Task 65.3)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest
from tokemetry_client import (
    AsyncIngestClient,
    IngestAuthError,
    IngestClient,
    IngestRetryError,
    UsageEventV2,
)


def _event(event_id: str) -> UsageEventV2:
    # model_validate coerces the wire-shaped dict (string enums, ISO timestamp,
    # nested source) into the strictly-typed generated model, exactly as an
    # inbound payload would be parsed.
    return UsageEventV2.model_validate(
        {
            "schema_version": 2,
            "event_id": event_id,
            "event_kind": "attempt",
            "finality": "final",
            "sequence": 0,
            "provider": "anthropic",
            "native_model": "claude-opus-4-5",
            "ts_started": "2026-07-10T12:00:00Z",
            "source": {"type": "gateway", "name": "gw-1", "version": "1.0"},
        }
    )


def _client(handler: Callable[[httpx.Request], httpx.Response], **kwargs: object) -> IngestClient:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return IngestClient(
        "http://server",
        "t",
        client=http,
        sleep=lambda _seconds: None,
        random_fn=lambda: 0.5,
        **kwargs,  # type: ignore[arg-type]
    )


def _ok(accepted: int) -> httpx.Response:
    return httpx.Response(200, json={"accepted": accepted})


def test_batches_by_size_and_sends_envelope() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _ok(1)

    result = _client(handler, batch_size=2).ingest(
        [_event("a"), _event("b"), _event("c")]
    )
    assert result.batches == 2  # [a,b] then [c]
    assert calls[0].headers["authorization"] == "Bearer t"
    body = json.loads(calls[0].content)
    assert body["schema_version"] == 2
    assert len(body["events"]) == 2


def test_retries_on_429_then_succeeds() -> None:
    attempts = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(429) if attempts["n"] == 1 else _ok(1)

    result = _client(handler).ingest([_event("a")])
    assert attempts["n"] == 2
    assert result.accepted == 1


def test_retries_on_500_then_succeeds() -> None:
    attempts = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(503) if attempts["n"] == 1 else _ok(1)

    _client(handler).ingest([_event("a")])
    assert attempts["n"] == 2


def test_raises_after_exhausting_retries() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with pytest.raises(IngestRetryError):
        _client(handler, max_retries=2).ingest([_event("a")])


def test_pauses_on_401_without_retry() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401)

    with pytest.raises(IngestAuthError):
        _client(handler).ingest([_event("a")])
    assert calls["n"] == 1  # no retry on 401


def test_isolates_single_poison_event_on_422() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(422)

    result = _client(handler).ingest([_event("bad")])
    assert result.rejected == 1
    assert [e.event_id for e in result.poison_events] == ["bad"]


def test_bisects_a_rejected_batch_to_isolate_the_poison() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        ids = [e["event_id"] for e in json.loads(request.content)["events"]]
        return httpx.Response(400) if "bad" in ids else _ok(len(ids))

    result = _client(handler).ingest([_event("a"), _event("bad"), _event("c")])
    assert result.rejected == 1
    assert [e.event_id for e in result.poison_events] == ["bad"]
    assert result.accepted == 2


def _async_client(
    handler: Callable[[httpx.Request], httpx.Response], **kwargs: object
) -> AsyncIngestClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return AsyncIngestClient(
        "http://server",
        "t",
        client=http,
        sleep=_noop_sleep,
        random_fn=lambda: 0.5,
        **kwargs,  # type: ignore[arg-type]
    )


async def _noop_sleep(_seconds: float) -> None:
    return None


def test_async_batches_and_sends_envelope() -> None:
    calls: list[httpx.Request] = []

    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return _ok(1)

        result = await _async_client(handler, batch_size=2).ingest(
            [_event("a"), _event("b"), _event("c")]
        )
        assert result.batches == 2  # [a,b] then [c]
        assert calls[0].headers["authorization"] == "Bearer t"
        body = json.loads(calls[0].content)
        assert body["schema_version"] == 2
        assert len(body["events"]) == 2

    asyncio.run(scenario())


def test_async_retries_on_429_then_succeeds() -> None:
    attempts = {"n": 0}

    async def scenario() -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            return httpx.Response(429) if attempts["n"] == 1 else _ok(1)

        result = await _async_client(handler).ingest([_event("a")])
        assert attempts["n"] == 2
        assert result.accepted == 1

    asyncio.run(scenario())


def test_async_pauses_on_401_without_retry() -> None:
    calls = {"n": 0}

    async def scenario() -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(401)

        with pytest.raises(IngestAuthError):
            await _async_client(handler).ingest([_event("a")])
        assert calls["n"] == 1  # no retry on 401

    asyncio.run(scenario())


def test_async_bisects_a_rejected_batch_to_isolate_the_poison() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            ids = [e["event_id"] for e in json.loads(request.content)["events"]]
            return httpx.Response(400) if "bad" in ids else _ok(len(ids))

        result = await _async_client(handler).ingest(
            [_event("a"), _event("bad"), _event("c")]
        )
        assert result.rejected == 1
        assert [e.event_id for e in result.poison_events] == ["bad"]
        assert result.accepted == 2

    asyncio.run(scenario())
