"""Tests for runtime-editable alert channel configuration."""

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.config import Settings
from tokemetry_server.services.channel_config import (
    channel_views,
    resolve_channel_settings,
    save_channel_config,
)


async def test_resolve_no_overrides_returns_base(async_session: AsyncSession) -> None:
    base = Settings(telegram_bot_token=None)
    resolved = await resolve_channel_settings(async_session, base)
    assert resolved.telegram_bot_token is None


async def test_db_override_wins_over_env(async_session: AsyncSession) -> None:
    await save_channel_config(
        async_session,
        "telegram",
        {"telegram_bot_token": "secret123", "telegram_chat_id": "42"},
    )
    await async_session.commit()
    base = Settings(telegram_bot_token="envtoken", telegram_chat_id="env")
    resolved = await resolve_channel_settings(async_session, base)
    assert resolved.telegram_bot_token == "secret123"
    assert resolved.telegram_chat_id == "42"


async def test_clear_falls_back_to_env(async_session: AsyncSession) -> None:
    await save_channel_config(async_session, "telegram", {"telegram_bot_token": "secret123"})
    await async_session.commit()
    await save_channel_config(async_session, "telegram", {"telegram_bot_token": ""})
    await async_session.commit()
    base = Settings(telegram_bot_token="envtoken")
    resolved = await resolve_channel_settings(async_session, base)
    assert resolved.telegram_bot_token == "envtoken"


async def test_channel_views_masks_secret(async_session: AsyncSession) -> None:
    await save_channel_config(
        async_session, "telegram", {"telegram_bot_token": "abcdef123456"}
    )
    await async_session.commit()
    views = await channel_views(async_session, Settings(telegram_bot_token=None))
    telegram = next(v for v in views if v.name == "telegram")
    token = next(f for f in telegram.fields if f.name == "telegram_bot_token")
    assert token.value == "...3456"
    assert token.is_secret is True
    assert token.is_set is True


async def test_smtp_port_coerced_to_int(async_session: AsyncSession) -> None:
    await save_channel_config(async_session, "smtp", {"smtp_port": "2525"})
    await async_session.commit()
    resolved = await resolve_channel_settings(async_session, Settings())
    assert resolved.smtp_port == 2525


def test_channels_api_get_put_and_reconfigure(
    client: TestClient, auth: dict[str, str]
) -> None:
    data = client.get("/api/v1/alerts/channels", headers=auth).json()
    assert {c["name"] for c in data["channels"]} == {"ntfy", "telegram", "smtp"}

    resp = client.put(
        "/api/v1/alerts/channels/telegram",
        json={"telegram_bot_token": "bot123456", "telegram_chat_id": "42"},
        headers=auth,
    )
    assert resp.status_code == 200
    telegram = next(c for c in resp.json()["channels"] if c["name"] == "telegram")
    assert telegram["configured"] is True
    token = next(f for f in telegram["fields"] if f["name"] == "telegram_bot_token")
    assert token["value"] == "...3456"
    assert token["is_set"] is True
    # The notifier was hot-swapped, no restart required.
    engine = client.app.state.alert_engine  # type: ignore[attr-defined]
    assert engine._notifiers["telegram"].is_configured() is True


def test_channels_api_unknown_channel_404(
    client: TestClient, auth: dict[str, str]
) -> None:
    assert (
        client.put("/api/v1/alerts/channels/bogus", json={}, headers=auth).status_code
        == 404
    )
