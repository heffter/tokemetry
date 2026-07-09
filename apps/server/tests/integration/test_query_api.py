"""HTTP tests for the query API endpoints."""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

_MACHINE = {"name": "box-1", "platform": "linux", "collector_version": "0.1.0"}

# Timestamps are anchored to the real clock so the now-relative endpoints
# (limits history, blocks, burn rate) include the seeded rows regardless of
# the wall-clock time the suite runs at.
_BASE = (datetime.now(UTC) - timedelta(hours=1)).replace(microsecond=0)
_WIDE_RANGE = "from=2026-01-01&to=2026-12-31"


def _seed_events(client: TestClient, auth: dict[str, str]) -> None:
    events = [
        {
            "event_id": f"req_{i}",
            "provider": "anthropic",
            "native_model": "claude-opus-4-5",
            "ts": _BASE.isoformat(),
            "session_id": f"sess-{i % 2}",
            "project": "proj-a",
            "input_tokens": 1000,
            "output_tokens": 100,
        }
        for i in range(4)
    ]
    response = client.post(
        "/api/v1/ingest/events", json={"machine": _MACHINE, "events": events}, headers=auth
    )
    assert response.status_code == 200


def _seed_limits(client: TestClient, auth: dict[str, str]) -> None:
    snapshots = [
        {
            "provider": "anthropic",
            "ts": _BASE.isoformat(),
            "window_kind": "five_hour",
            "utilization_pct": 40.0,
            "resets_at": (_BASE + timedelta(hours=3)).isoformat(),
        }
    ]
    response = client.post(
        "/api/v1/ingest/limits", json={"machine": _MACHINE, "snapshots": snapshots}, headers=auth
    )
    assert response.status_code == 200


def _get(client: TestClient, auth: dict[str, str], url: str) -> Any:
    response = client.get(url, headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


def test_endpoints_require_auth(client: TestClient) -> None:
    for url in ("/api/v1/summary/now", "/api/v1/usage", "/api/v1/machines", "/api/v1/pricing"):
        assert client.get(url).status_code == 401


def test_summary_now(client: TestClient, auth: dict[str, str]) -> None:
    _seed_events(client, auth)
    _seed_limits(client, auth)

    data = _get(client, auth, "/api/v1/summary/now")

    assert "limits" in data
    assert any(limit["window_kind"] == "five_hour" for limit in data["limits"])
    assert "token_burn_rate_per_min" in data
    assert "today" in data


def test_usage_grouped_by_model(client: TestClient, auth: dict[str, str]) -> None:
    _seed_events(client, auth)
    data = _get(client, auth, f"/api/v1/usage?group_by=model&{_WIDE_RANGE}")
    assert data["group_by"] == "model"
    opus = next(b for b in data["buckets"] if b["key"] == "claude-opus-4-5")
    assert opus["total_tokens"] == 4 * 1100


def test_usage_by_session(client: TestClient, auth: dict[str, str]) -> None:
    _seed_events(client, auth)
    data = _get(client, auth, f"/api/v1/usage?group_by=session&{_WIDE_RANGE}")
    keys = {bucket["key"] for bucket in data["buckets"]}
    assert keys == {"sess-0", "sess-1"}


def test_sessions_and_machines(client: TestClient, auth: dict[str, str]) -> None:
    _seed_events(client, auth)
    sessions = _get(client, auth, "/api/v1/sessions")
    assert len(sessions) == 2
    machines = _get(client, auth, "/api/v1/machines")
    assert machines[0]["id"] == "box-1"
    assert machines[0]["event_count"] == 4


def test_limits_current_and_history(client: TestClient, auth: dict[str, str]) -> None:
    _seed_limits(client, auth)
    current = _get(client, auth, "/api/v1/limits/current")
    assert current[0]["window_kind"] == "five_hour"
    history = _get(client, auth, "/api/v1/limits/history?window_kind=five_hour&hours=720")
    assert len(history) >= 1


def test_heatmap_cost_pricing(client: TestClient, auth: dict[str, str]) -> None:
    _seed_events(client, auth)
    heatmap = _get(client, auth, f"/api/v1/heatmap?{_WIDE_RANGE}")
    assert "calendar" in heatmap
    assert "punch_card" in heatmap
    cost = _get(client, auth, f"/api/v1/cost?{_WIDE_RANGE}")
    assert "total_cost_usd" in cost
    pricing = _get(client, auth, "/api/v1/pricing")
    assert any(row["model"] == "claude-opus-4-5" for row in pricing)


def test_blocks(client: TestClient, auth: dict[str, str]) -> None:
    _seed_events(client, auth)
    _seed_limits(client, auth)
    blocks = _get(client, auth, "/api/v1/blocks?hours=2400")
    assert isinstance(blocks, list)
    assert blocks and blocks[-1]["total_tokens"] > 0


def _seed_project_variants(client: TestClient, auth: dict[str, str]) -> None:
    """Ingest events whose cwd variants all belong to one project 'Foo'."""
    paths = [
        r"C:\devel\Foo",
        r"c:\devel\Foo",  # case-variant drive letter
        r"C:\devel\Foo\.claude\worktrees\wt-1\apps",  # worktree subpath
    ]
    events = [
        {
            "event_id": f"proj_{i}",
            "provider": "anthropic",
            "native_model": "claude-opus-4-5",
            "ts": _BASE.isoformat(),
            "session_id": f"psess-{i}",
            "project": path,
            "input_tokens": 500,
            "output_tokens": 50,
        }
        for i, path in enumerate(paths)
    ]
    response = client.post(
        "/api/v1/ingest/events", json={"machine": _MACHINE, "events": events}, headers=auth
    )
    assert response.status_code == 200


def test_project_grouping_folds_variants(client: TestClient, auth: dict[str, str]) -> None:
    _seed_project_variants(client, auth)
    data = _get(client, auth, f"/api/v1/usage?group_by=project&{_WIDE_RANGE}")
    foo = [b for b in data["buckets"] if b["key"] == "Foo"]
    assert len(foo) == 1, data["buckets"]
    assert foo[0]["total_tokens"] == 3 * 550
    # Sessions expose the same normalized project label.
    sessions = _get(client, auth, "/api/v1/sessions")
    assert all(s["project"] == "Foo" for s in sessions if s["session_id"].startswith("psess"))


def test_rebuild_rollups_endpoint(client: TestClient, auth: dict[str, str]) -> None:
    _seed_project_variants(client, auth)
    response = client.post("/api/v1/admin/rebuild-rollups", headers=auth)
    assert response.status_code == 200, response.text
    assert response.json()["rollups_rebuilt"] >= 1
    # Grouping still holds after a full rebuild.
    data = _get(client, auth, f"/api/v1/usage?group_by=project&{_WIDE_RANGE}")
    assert any(b["key"] == "Foo" for b in data["buckets"])


def _assert_aware(value: str) -> None:
    """A serialized datetime must carry an explicit UTC offset."""
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None, f"naive datetime serialized: {value!r}"


def test_response_datetimes_are_offset_aware(client: TestClient, auth: dict[str, str]) -> None:
    _seed_events(client, auth)
    _seed_limits(client, auth)

    summary = _get(client, auth, "/api/v1/summary/now")
    _assert_aware(summary["now"])
    for limit in summary["limits"]:
        _assert_aware(limit["ts"])
        if limit["resets_at"] is not None:
            _assert_aware(limit["resets_at"])

    for limit in _get(client, auth, "/api/v1/limits/current"):
        _assert_aware(limit["ts"])
        if limit["resets_at"] is not None:
            _assert_aware(limit["resets_at"])

    for session in _get(client, auth, "/api/v1/sessions"):
        _assert_aware(session["started_at"])
        _assert_aware(session["last_at"])

    for block in _get(client, auth, "/api/v1/blocks?hours=2400"):
        _assert_aware(block["start"])
        _assert_aware(block["end"])
