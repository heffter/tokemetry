"""The shipped example config parses and the new sources are off by default."""

import tomllib
from pathlib import Path

from tokemetry_collector.config import CollectorConfig
from tokemetry_collector.sources import build_limit_sources

_EXAMPLE = Path(__file__).parents[4] / "deploy" / "collector.example.toml"


def test_example_config_parses() -> None:
    data = tomllib.loads(_EXAMPLE.read_text(encoding="utf-8"))
    config = CollectorConfig.model_validate(data)
    assert config.machine_name


def test_new_limit_sources_present_and_disabled_by_default() -> None:
    data = tomllib.loads(_EXAMPLE.read_text(encoding="utf-8"))
    config = CollectorConfig.model_validate(data)
    assert config.limits["openai_codex"].enabled is False
    assert config.limits["zai_coding_plan"].enabled is False
    # Only the enabled anthropic_oauth source is actually built.
    providers = {source.provider for source in build_limit_sources(config)}
    assert providers == {"anthropic"}
