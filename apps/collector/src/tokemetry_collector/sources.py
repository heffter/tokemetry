"""Build usage and limit sources from configuration.

Maps enabled source names in the config to concrete implementations. New
providers register a builder here; the runner stays provider-agnostic. The
built-in builders are registered at import time.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from tokemetry_core.interfaces import LimitsSource, UsageSource
from tokemetry_core.providers.claude_code import ClaudeCodeJsonlSource

from tokemetry_collector.config import CollectorConfig, SourceConfig

#: Registry of usage-source builders keyed by config source name.
UsageSourceBuilder = Callable[[CollectorConfig, SourceConfig], UsageSource]
#: Registry of limit-source builders keyed by config limit name.
LimitsSourceBuilder = Callable[[CollectorConfig, SourceConfig], LimitsSource]

_USAGE_BUILDERS: dict[str, UsageSourceBuilder] = {}
_LIMITS_BUILDERS: dict[str, LimitsSourceBuilder] = {}


def _build_claude_code(config: CollectorConfig, source_cfg: SourceConfig) -> UsageSource:
    """Build the Claude Code JSONL usage source from config.

    Honors an optional ``claude_home`` override; otherwise the source
    resolves ``CLAUDE_CONFIG_DIR`` or ``~/.claude`` itself. The machine name
    is stamped so events are attributed to this collector.
    """
    raw_home = (source_cfg.model_extra or {}).get("claude_home")
    claude_home = Path(str(raw_home)) if raw_home else None
    return ClaudeCodeJsonlSource(claude_home=claude_home, machine=config.machine_name)


def register_usage_builder(name: str, builder: UsageSourceBuilder) -> None:
    """Register a usage-source builder under a config source name."""
    _USAGE_BUILDERS[name] = builder


def register_limits_builder(name: str, builder: LimitsSourceBuilder) -> None:
    """Register a limit-source builder under a config limit name."""
    _LIMITS_BUILDERS[name] = builder


def build_usage_sources(config: CollectorConfig) -> list[UsageSource]:
    """Instantiate every enabled, known usage source from config."""
    sources: list[UsageSource] = []
    for name in config.enabled_sources():
        builder = _USAGE_BUILDERS.get(name)
        if builder is not None:
            sources.append(builder(config, config.sources[name]))
    return sources


def build_limit_sources(config: CollectorConfig) -> list[LimitsSource]:
    """Instantiate every enabled, known limit source from config."""
    sources: list[LimitsSource] = []
    for name in config.enabled_limits():
        builder = _LIMITS_BUILDERS.get(name)
        if builder is not None:
            sources.append(builder(config, config.limits[name]))
    return sources


#: The config key under which the Claude Code usage source is enabled.
CLAUDE_CODE_SOURCE = "claude_code"

register_usage_builder(CLAUDE_CODE_SOURCE, _build_claude_code)
