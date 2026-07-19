"""The provider-neutral rate-card row a price source produces (D-006).

A :class:`RateCardRow` mirrors one ``rate_cards`` grain -- a single ``unit_type``
price for a model, effective from a date -- without depending on the server ORM,
so price sources live in ``tokemetry_core`` and the server maps rows onto
``models.RateCard`` at import time. ``unit_price`` is per single unit (per token
for token units), matching the ``rate_cards`` column semantics; ``source`` and
``priority`` carry provenance and precedence (``official`` outranks ``litellm``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True)
class RateCardRow:
    """One per-unit price for a model, effective from a date (D-006)."""

    provider: str
    native_model: str
    unit_type: str
    effective_from: date
    unit_price: Decimal
    source: str
    currency: str = "USD"
    mode: str = "realtime"
    service_tier: str | None = None
    context_bracket: str | None = None
    region: str | None = None
    priority: int = 0
    override: bool = False
    verified_at: datetime | None = None
