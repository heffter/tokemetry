"""Fetch and persist Anthropic prices from LiteLLM's public price database.

The fetch is isolated here (the only network dependency of pricing) so it is
easy to mock. Parsing/transformation lives in
``tokemetry_core.pricing.litellm``; persistence in ``pricing_repo``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import httpx
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.pricing.litellm import price_rows_from_litellm
from tokemetry_core.pricing.sources.curated import curated_rate_cards
from tokemetry_core.pricing.sources.litellm import rate_cards_from_litellm
from tokemetry_core.pricing.sources.rate_card import RateCardRow

from tokemetry_server.services.pricing_repo import upsert_price_rows

#: Canonical raw URL of LiteLLM's machine-readable price database.
LITELLM_PRICES_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)


async def fetch_litellm_prices(client: httpx.AsyncClient) -> dict[str, object]:
    """Fetch and parse the LiteLLM price database JSON.

    Raises:
        httpx.HTTPError: On network failure or non-2xx response.
    """
    response = await client.get(LITELLM_PRICES_URL, timeout=30.0)
    response.raise_for_status()
    data: dict[str, object] = response.json()
    return data


async def sync_anthropic_pricing(
    session: AsyncSession,
    dialect_name: str,
    data: dict[str, object],
    effective_date: date,
) -> int:
    """Transform LiteLLM data and upsert Anthropic price rows.

    Args:
        session: Active session (caller owns the transaction).
        dialect_name: Dialect for the upsert syntax.
        data: Parsed LiteLLM price database.
        effective_date: Date the synced prices become effective.

    Returns:
        Number of price rows written.
    """
    rows = price_rows_from_litellm(data, effective_date, provider="anthropic")
    count = await upsert_price_rows(session, dialect_name, rows, source="litellm")
    logger.info("synced {} Anthropic price rows from LiteLLM", count)
    return count


def import_rate_cards_from_data(
    data: dict[str, Any], effective_from: date, verified_at: datetime | None
) -> list[RateCardRow]:
    """Build the v2 rate-card import set: LiteLLM rows plus curated official rows.

    LiteLLM covers Anthropic and OpenAI; the curated official source supplies
    Z.ai (a LiteLLM gap) and any hand-verified overrides at a higher priority.
    """
    rows = rate_cards_from_litellm(data, effective_from, verified_at)
    rows.extend(curated_rate_cards(effective_from, verified_at))
    return rows
