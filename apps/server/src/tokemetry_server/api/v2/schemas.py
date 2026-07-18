"""Response schemas for the v2 registry API.

Wire models for the provider and model registries. Kept separate from storage
so the API contract (FR-PROVIDER-010, FR-MODEL-010) evolves independently of
the ORM. All models forbid extra fields so response drift fails loudly in
tests.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from tokemetry_server.api.serialization import UtcDatetime


class ProviderOut(BaseModel):
    """Registry metadata for one provider (FR-PROVIDER-010)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    display_name: str
    aliases: list[str]
    pricing_strategy: str
    limit_semantics: str
    supported_dimensions: list[str]
    registered: bool


class ModelOut(BaseModel):
    """Registry metadata for one model, with its alias spellings (FR-MODEL-010).

    ``native_model_id`` is the provider's own id; ``aliases`` are the alternate
    spellings that normalize to it, so consumers see both the native and the
    normalized forms where both exist.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str
    native_model_id: str
    lifecycle: str
    capabilities: dict[str, Any]
    first_seen: UtcDatetime | None
    last_seen: UtcDatetime | None
    aliases: list[str]
