"""Per-session agent hierarchy (Task 75, FR-TRACE-009)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.db import models
from tokemetry_server.services.agent_hierarchy import session_agents
from tokemetry_server.services.trace_queries import scoped_session_id

_TS = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
_S1 = "1111111111111111"
_S2 = "2222222222222222"
_S3 = "3333333333333333"
_S4 = "4444444444444444"


def _event(
    event_id: str, agent: str, span: str, parent_span: str | None
) -> models.UsageEventV2:
    return models.UsageEventV2(
        provider="anthropic",
        event_id=event_id,
        schema_version=2,
        event_kind="attempt",
        finality="final",
        sequence=0,
        native_model="claude-sonnet-4-5",
        ts_started=_TS,
        session_id="s1",
        agent_id=agent,
        span_id=span,
        parent_span_id=parent_span,
        source_id=1,
        input_tokens=0,
        output_tokens=10,
        cache_read_tokens=0,
        cache_write_short_tokens=0,
        cache_write_long_tokens=0,
        reasoning_tokens=0,
        success=True,
        tool_call_count=0,
        provenance="official",
        dimensions={},
        extra={},
    )


async def _seed(session: AsyncSession) -> None:
    session.add(
        models.Source(
            id=1, type="gateway", name="proxy", first_seen=_TS, last_seen=_TS
        )
    )
    session.add(_event("e1", "root", _S1, None))
    session.add(_event("e2", "child", _S2, _S1))
    session.add(_event("e3", "child", _S3, _S1))
    session.add(_event("e4", "grandchild", _S4, _S2))
    await session.commit()


async def test_session_agent_hierarchy(async_session: AsyncSession) -> None:
    await _seed(async_session)
    nodes = await session_agents(async_session, "anthropic", "proxy", "s1")
    by_id = {n.agent_id: n for n in nodes}

    assert by_id["root"].depth == 0
    assert by_id["root"].parent_agent_id is None
    assert by_id["root"].attempt_count == 1

    assert by_id["child"].depth == 1
    assert by_id["child"].parent_agent_id == "root"
    assert by_id["child"].attempt_count == 2  # two calls by the child agent

    assert by_id["grandchild"].depth == 2
    assert by_id["grandchild"].parent_agent_id == "child"

    # Roots-first ordering.
    assert nodes[0].agent_id == "root"


async def test_session_without_agents_is_empty(async_session: AsyncSession) -> None:
    async_session.add(
        models.Source(
            id=1, type="gateway", name="proxy", first_seen=_TS, last_seen=_TS
        )
    )
    event = _event("e1", "root", _S1, None)
    event.agent_id = None  # no agent metadata
    async_session.add(event)
    await async_session.commit()
    assert await session_agents(async_session, "anthropic", "proxy", "s1") == []


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #

def _wire(event_id: str, agent: str, span: str, parent: str | None) -> dict[str, Any]:
    event: dict[str, Any] = {
        "schema_version": 2,
        "event_id": event_id,
        "event_kind": "attempt",
        "finality": "final",
        "sequence": 1,
        "provider": "anthropic",
        "native_model": "claude-sonnet-4-5",
        "ts_started": "2026-07-10T12:00:00Z",
        "session_id": "s1",
        "agent_id": agent,
        "span_id": span,
        "output_tokens": 10,
        "source": {"type": "gateway", "name": "proxy", "version": "1"},
    }
    if parent is not None:
        event["parent_span_id"] = parent
    return event


def test_agents_endpoint(client: TestClient, auth: dict[str, str]) -> None:
    response = client.post(
        "/api/v2/ingest/events",
        json={
            "schema_version": 2,
            "events": [
                _wire("e1", "root", _S1, None),
                _wire("e2", "child", _S2, _S1),
            ],
        },
        headers=auth,
    )
    assert response.status_code == 200, response.text

    # The attempts surface now exposes agent_id.
    attempts = client.get(
        "/api/v2/attempts",
        params={"from": "2026-07-01T00:00:00Z", "to": "2026-08-01T00:00:00Z"},
        headers=auth,
    ).json()["attempts"]
    assert {a["agent_id"] for a in attempts} == {"root", "child"}

    scoped_id = scoped_session_id("anthropic", "proxy", "s1")
    body = client.get(f"/api/v2/sessions/{scoped_id}/agents", headers=auth)
    assert body.status_code == 200, body.text
    agents = {a["agent_id"]: a for a in body.json()["agents"]}
    assert agents["root"]["depth"] == 0
    assert agents["child"]["depth"] == 1
    assert agents["child"]["parent_agent_id"] == "root"
