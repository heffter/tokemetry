"""Collector configuration loaded from a TOML file.

A single TOML file describes where to send data, which machine this is, how
often to poll, and which sources/limits are enabled. Source-specific options
are kept as raw dicts here and interpreted when sources are constructed, so
adding a provider needs no change to this schema.
"""

from __future__ import annotations

import platform
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SourceConfig(BaseModel):
    """Per-source enable flag plus arbitrary source-specific options."""

    model_config = ConfigDict(extra="allow")

    enabled: bool = True


class CollectorConfig(BaseModel):
    """Top-level collector configuration."""

    model_config = ConfigDict(extra="forbid")

    server_url: str = Field(min_length=1)
    api_token: str = Field(min_length=1)
    machine_name: str = Field(min_length=1)
    machine_platform: str = Field(default_factory=platform.system)
    poll_interval_seconds: float = Field(default=60.0, gt=0)
    limits_poll_interval_seconds: float = Field(default=120.0, gt=0)
    upload_batch_size: int = Field(default=500, gt=0)
    state_db_path: Path = Field(default=Path("tokemetry-collector-state.sqlite3"))
    sources: dict[str, SourceConfig] = Field(default_factory=dict)
    limits: dict[str, SourceConfig] = Field(default_factory=dict)

    def enabled_sources(self) -> list[str]:
        """Names of enabled usage sources."""
        return [name for name, cfg in self.sources.items() if cfg.enabled]

    def enabled_limits(self) -> list[str]:
        """Names of enabled limit sources."""
        return [name for name, cfg in self.limits.items() if cfg.enabled]


def load_config(path: Path) -> CollectorConfig:
    """Load and validate collector configuration from a TOML file.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        ValueError: If the TOML is malformed or fails validation.
    """
    raw: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
    return CollectorConfig.model_validate(raw)
