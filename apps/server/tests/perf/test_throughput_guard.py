"""Unit tests for the load-aware throughput gate (Task 79).

These run in the default gate (no ``perf`` marker) and cover the two behaviours
the perf smoke test relies on -- catching a real regression and skipping under
load -- with mocked load metrics and no wall-clock timing, so they are
deterministic on any machine.
"""

from __future__ import annotations

import psutil
import pytest

from .throughput_guard import (
    MAX_CPU_PERCENT,
    MAX_MEM_PERCENT,
    MIN_RATE,
    machine_load_reason,
    throughput_regression,
)


class _Mem:
    """Minimal stand-in for ``psutil.virtual_memory()`` exposing ``percent``."""

    def __init__(self, percent: float) -> None:
        self.percent = percent


def test_regression_below_floor_is_caught() -> None:
    # A real regression to 50 events/s must be reported (acceptance criterion).
    assert throughput_regression(50.0) is not None
    # The floor is exclusive: exactly MIN_RATE still counts as a regression.
    assert throughput_regression(MIN_RATE) is not None


def test_healthy_throughput_passes() -> None:
    assert throughput_regression(MIN_RATE + 1.0) is None
    assert throughput_regression(1000.0) is None


def test_high_memory_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(psutil, "virtual_memory", lambda: _Mem(MAX_MEM_PERCENT + 1.0))
    monkeypatch.setattr(psutil, "cpu_percent", lambda interval: 0.0)
    reason = machine_load_reason()
    assert reason is not None and "RAM" in reason


def test_high_cpu_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(psutil, "virtual_memory", lambda: _Mem(10.0))
    monkeypatch.setattr(psutil, "cpu_percent", lambda interval: MAX_CPU_PERCENT + 1.0)
    reason = machine_load_reason()
    assert reason is not None and "CPU" in reason


def test_idle_machine_enforces_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(psutil, "virtual_memory", lambda: _Mem(10.0))
    monkeypatch.setattr(psutil, "cpu_percent", lambda interval: 5.0)
    # An idle machine runs the floor check, which then catches a regression.
    assert machine_load_reason() is None
    assert throughput_regression(50.0) is not None
