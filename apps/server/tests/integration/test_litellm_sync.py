"""Integration tests for LiteLLM price fetching and syncing."""

from datetime import date
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.services.litellm_sync import (
    fetch_litellm_prices,
    sync_anthropic_pricing,
)
from tokemetry_server.services.pricing_repo import load_pricing_table

_FIXTURE: dict[str, Any] = {
    "claude-opus-4-5-20251101": {
        "litellm_provider": "anthropic",
        "input_cost_per_token": 5e-06,
        "output_cost_per_token": 2.5e-05,
        "cache_read_input_token_cost": 5e-07,
    },
    "gpt-5": {
        "litellm_provider": "openai",
        "input_cost_per_token": 1e-06,
        "output_cost_per_token": 4e-06,
    },
}


async def test_fetch_parses_json() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_FIXTURE)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        data = await fetch_litellm_prices(client)
    assert "claude-opus-4-5-20251101" in data


async def test_sync_inserts_only_anthropic(async_session: AsyncSession) -> None:
    count = await sync_anthropic_pricing(async_session, "sqlite", _FIXTURE, date(2026, 7, 1))
    await async_session.commit()
    assert count == 1  # openai model excluded

    table = await load_pricing_table(async_session)
    row = table.resolve("anthropic", "claude-opus-4-5-20251101", date(2026, 7, 1))
    assert row.output_per_mtok == Decimal("25")
    assert row.cache_read_per_mtok == Decimal("0.5")
