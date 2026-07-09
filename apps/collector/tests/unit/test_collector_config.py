"""Unit tests for collector configuration loading."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from tokemetry_collector.config import CollectorConfig, load_config

_TOML = """
server_url = "http://10.0.0.1:8787"
api_token = "tkm_secret"
machine_name = "box-1"
poll_interval_seconds = 30

[sources.claude_code]
enabled = true

[sources.disabled_one]
enabled = false

[limits.anthropic_oauth]
enabled = true
poll_interval_seconds = 90
"""


def test_load_valid_config(tmp_path: Path) -> None:
    path = tmp_path / "collector.toml"
    path.write_text(_TOML, encoding="utf-8")

    config = load_config(path)

    assert config.server_url == "http://10.0.0.1:8787"
    assert config.machine_name == "box-1"
    assert config.poll_interval_seconds == 30
    assert config.enabled_sources() == ["claude_code"]
    assert config.enabled_limits() == ["anthropic_oauth"]


def test_machine_platform_defaults_to_system(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text(
        'server_url="u"\napi_token="t"\nmachine_name="m"\n', encoding="utf-8"
    )
    config = load_config(path)
    assert config.machine_platform  # non-empty platform string


def test_missing_required_field_rejected() -> None:
    with pytest.raises(ValidationError):
        CollectorConfig.model_validate({"server_url": "u", "api_token": "t"})


def test_unknown_top_level_key_rejected() -> None:
    with pytest.raises(ValidationError):
        CollectorConfig.model_validate(
            {"server_url": "u", "api_token": "t", "machine_name": "m", "bogus": 1}
        )
