"""Smoke tests for the tokemetry_collector package scaffold."""

from tokemetry_collector.cli import main


def test_main_returns_success() -> None:
    """The placeholder CLI exits cleanly."""
    assert main() == 0
