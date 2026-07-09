"""Fetch and persist Anthropic prices from LiteLLM's public price database.

The fetch is isolated here (the only network dependency of pricing) so it is
easy to mock. Parsing/transformation lives in
``tokemetry_core.pricing.litellm``; persistence in ``pricing_repo``.
"""

from __future__ import annotations

from datetime import date

import httpx
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.pricing.litellm import price_rows_from_litellm

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
