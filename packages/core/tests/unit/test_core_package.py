"""Smoke tests for the tokemetry_core package scaffold."""

import tokemetry_core


def test_version_is_exposed() -> None:
    """The package exposes a semver version string."""
    assert tokemetry_core.__version__.count(".") == 2
