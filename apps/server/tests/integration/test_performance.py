"""Performance smoke tests for v2 ingest (PRD Section 18.5, NFR-PERF-001/002).

Marked ``perf`` so they can be selected (``pytest -m perf``) or run as part of
the suite. The wall-clock bounds are deliberately generous -- they catch
pathological regressions, not hardware differences -- while the measured numbers
are printed (run with ``-s``) and recorded in the task log and docs. The strict
NFR-PERF-001 (100-event p95 < 200 ms) and NFR-PERF-002 (1000 events/s) gates run
on reference hardware in Task 70.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient


def _v2_event(event_id: str) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "event_id": event_id,
        "event_kind": "attempt",
        "finality": "final",
        "sequence": 1,
        "provider": "anthropic",
        "native_model": "claude-sonnet-4-5",
        "ts_started": "2026-07-10T12:00:00Z",
        "output_tokens": 100,
        "source": {"type": "gateway", "name": "proxy", "version": "1.0"},
    }


def _v1_event(event_id: str) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "provider": "anthropic",
        "native_model": "claude-sonnet-4-5",
        "ts": "2026-07-10T12:00:00Z",
        "output_tokens": 100,
    }


def _percentile(samples: list[float], pct: float) -> float:
    ordered = sorted(samples)
    index = min(len(ordered) - 1, round((pct / 100.0) * (len(ordered) - 1)))
    return ordered[index]


@pytest.mark.perf
def test_100_event_batch_p95(client: TestClient, auth: dict[str, str]) -> None:
    """A 100-event v2 batch stays well within the ingest budget (NFR-PERF-001)."""
    durations: list[float] = []
    for iteration in range(12):
        batch = {
            "schema_version": 2,
            "events": [_v2_event(f"perf-{iteration}-{i}") for i in range(100)],
        }
        start = time.perf_counter()
        response = client.post("/api/v2/ingest/events", json=batch, headers=auth)
        durations.append(time.perf_counter() - start)
        assert response.status_code == 200
        assert response.json()["accepted"] == 100

    p95 = _percentile(durations, 95)
    print(f"\n[perf] 100-event batch p95 = {p95 * 1000:.1f} ms")
    # Generous ceiling; the strict 200 ms gate is on reference hardware (Task 70).
    assert p95 < 3.0


@pytest.mark.perf
def test_5000_event_v1_compatibility_batch(client: TestClient, auth: dict[str, str]) -> None:
    """A 5000-event v1 batch maps into the v2 ledger in one call (PRD 18.5)."""
    batch = {
        "machine": {"name": "perf-box"},
        "events": [_v1_event(f"perf5k-{i}") for i in range(5000)],
    }
    start = time.perf_counter()
    response = client.post("/api/v1/ingest/events", json=batch, headers=auth)
    elapsed = time.perf_counter() - start
    print(f"\n[perf] 5000-event v1 batch = {elapsed:.2f} s")
    assert response.status_code == 200
    assert response.json()["accepted"] == 5000
    assert elapsed < 60.0


@pytest.mark.perf
def test_sustained_throughput_smoke(client: TestClient, auth: dict[str, str]) -> None:
    """Sustained back-to-back batches keep a healthy events/second rate."""
    total_events = 0
    start = time.perf_counter()
    for iteration in range(10):
        batch = {
            "schema_version": 2,
            "events": [_v2_event(f"sust-{iteration}-{i}") for i in range(100)],
        }
        response = client.post("/api/v2/ingest/events", json=batch, headers=auth)
        assert response.status_code == 200
        total_events += 100
    elapsed = time.perf_counter() - start
    rate = total_events / elapsed
    print(f"\n[perf] sustained ingest = {rate:.0f} events/s ({total_events} events)")
    # Loose floor preparing the full 1000 events/s gate (Task 70).
    assert rate > 50.0
