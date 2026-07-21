"""Trace/span linkage end to end (Task 71.1, FR-OTEL-001, FR-TRACE-008/009).

Attempts carry trace-context ids through ingest to the attempt query surface;
``trace_id`` filters group a trace, ``parent_span_id`` resolves the parent, and
a malformed id passes through with a data-quality note.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient

_RANGE = {"from": "2026-07-01T00:00:00Z", "to": "2026-08-01T00:00:00Z"}
_TRACE = "0af7651916cd43dd8448eb211c80319c"
_PARENT_SPAN = "b7ad6b7169203331"
_CHILD_SPAN = "00f067aa0ba902b7"


def _event(event_id: str, span_id: str, parent: str | None, **over: Any) -> dict[str, Any]:
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
        "trace_id": _TRACE,
        "span_id": span_id,
        "parent_span_id": parent,
        "source": {"type": "sdk", "name": "otel", "version": "1"},
    }
    event.update(over)
    return event


def _ingest(client: TestClient, auth: dict[str, str], events: list[dict[str, Any]]) -> None:
    response = client.post(
        "/api/v2/ingest/events",
        json={"schema_version": 2, "events": events},
        headers=auth,
    )
    assert response.status_code == 200, response.text


def _attempts(
    client: TestClient, auth: dict[str, str], **params: str
) -> list[dict[str, Any]]:
    response = client.get(
        "/api/v2/attempts", params={**_RANGE, **params}, headers=auth
    )
    assert response.status_code == 200, response.text
    return list(response.json()["attempts"])


def test_trace_fields_exposed_and_grouped(
    client: TestClient, auth: dict[str, str]
) -> None:
    _ingest(
        client,
        auth,
        [
            _event("anthropic:parent", _PARENT_SPAN, None),
            _event("anthropic:child", _CHILD_SPAN, _PARENT_SPAN),
        ],
    )
    grouped = _attempts(client, auth, trace_id=_TRACE)
    assert {a["event_id"] for a in grouped} == {"anthropic:parent", "anthropic:child"}

    by_id = {a["event_id"]: a for a in grouped}
    child = by_id["anthropic:child"]
    parent = by_id["anthropic:parent"]
    # The fields are exposed and the parent resolves via parent_span_id.
    assert child["trace_id"] == _TRACE
    assert child["span_id"] == _CHILD_SPAN
    assert child["parent_span_id"] == parent["span_id"]
    assert parent["parent_span_id"] is None


def test_trace_id_filter_scopes_results(
    client: TestClient, auth: dict[str, str]
) -> None:
    _ingest(client, auth, [_event("anthropic:t1", _PARENT_SPAN, None)])
    _ingest(
        client,
        auth,
        [_event("anthropic:other", _CHILD_SPAN, None, trace_id="a" * 32)],
    )
    only = _attempts(client, auth, trace_id=_TRACE)
    assert [a["event_id"] for a in only] == ["anthropic:t1"]


def test_malformed_trace_id_passes_through_with_dq(
    client: TestClient, auth: dict[str, str], read_engine: sa.Engine
) -> None:
    """A non-conforming id is stored as-is and raises a data-quality note."""
    _ingest(
        client,
        auth,
        [_event("anthropic:bad", _PARENT_SPAN, None, trace_id="not-a-valid-trace")],
    )
    (attempt,) = _attempts(client, auth)
    assert attempt["trace_id"] == "not-a-valid-trace"  # lenient passthrough

    with read_engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT subject FROM data_quality_events "
                "WHERE kind = 'trace_context_malformed'"
            )
        ).all()
    assert len(rows) == 1
    assert rows[0].subject == "anthropic:bad"


def test_index_on_trace_columns(client: TestClient, read_engine: sa.Engine) -> None:
    # Requesting ``client`` runs the app lifespan, which migrates the DB.
    names = {
        idx["name"]
        for idx in sa.inspect(read_engine).get_indexes("usage_events_v2")
    }
    assert {
        "ix_usage_events_v2_trace_id",
        "ix_usage_events_v2_span_id",
        "ix_usage_events_v2_parent_span_id",
    } <= names
