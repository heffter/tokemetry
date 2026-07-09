"""Smoke tests for the tokemetry_server package scaffold."""

import tokemetry_server


def test_version_is_exposed() -> None:
    """The package exposes a semver version string."""
    assert tokemetry_server.__version__.count(".") == 2
