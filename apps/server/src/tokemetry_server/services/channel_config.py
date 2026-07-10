"""Runtime-editable alert channel configuration.

Channel settings (ntfy/telegram/smtp) may be edited from the UI and stored in
the ``app_settings`` table under ``channel.<field>`` keys. On read they are
merged over the environment ``Settings`` -- a non-empty DB value wins, an absent
or blank key falls back to env -- so secrets can still be provisioned by
environment for headless/first-run deployments while the UI can override them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.config import Settings
from tokemetry_server.db import models

#: Prefix for channel-config keys in the app_settings KV table.
KEY_PREFIX = "channel."

#: Fields editable per channel (attribute names on Settings).
CHANNEL_FIELDS: dict[str, tuple[str, ...]] = {
    "ntfy": ("ntfy_url", "ntfy_topic", "dashboard_url"),
    "telegram": ("telegram_bot_token", "telegram_chat_id"),
    "smtp": (
        "smtp_host",
        "smtp_port",
        "smtp_user",
        "smtp_password",
        "smtp_from",
        "smtp_to",
        "smtp_use_tls",
    ),
}

#: Fields masked when returned to the UI.
SECRET_FIELDS = frozenset({"telegram_bot_token", "smtp_password"})
_INT_FIELDS = frozenset({"smtp_port"})
_BOOL_FIELDS = frozenset({"smtp_use_tls"})


def _coerce(field: str, value: str) -> Any:
    """Coerce a stored string to the Settings field's Python type."""
    if field in _INT_FIELDS:
        try:
            return int(value)
        except ValueError:
            return value
    if field in _BOOL_FIELDS:
        return value.strip().lower() in ("1", "true", "yes", "on")
    return value


def _mask(value: str) -> str:
    """Mask a secret, revealing only the last 4 characters."""
    return f"...{value[-4:]}" if len(value) > 4 else "****"


async def resolve_channel_settings(session: AsyncSession, base: Settings) -> Settings:
    """Return ``base`` with any non-empty channel overrides from the DB applied."""
    rows = (
        await session.execute(
            select(models.AppSetting).where(
                models.AppSetting.key.like(f"{KEY_PREFIX}%")
            )
        )
    ).scalars()
    overrides: dict[str, Any] = {}
    for row in rows:
        field = row.key[len(KEY_PREFIX) :]
        if row.value != "" and field in {f for fs in CHANNEL_FIELDS.values() for f in fs}:
            overrides[field] = _coerce(field, row.value)
    return base.model_copy(update=overrides) if overrides else base


def _channel_configured(name: str, settings: Settings) -> bool:
    """Mirror the notifiers' is_configured checks for the given channel."""
    if name == "ntfy":
        return bool(settings.ntfy_topic)
    if name == "telegram":
        return bool(settings.telegram_bot_token and settings.telegram_chat_id)
    if name == "smtp":
        return bool(settings.smtp_host and settings.smtp_from and settings.smtp_to)
    return False


@dataclass(frozen=True)
class ChannelFieldView:
    """One channel field as shown in the UI (secrets masked)."""

    name: str
    value: str
    is_secret: bool
    is_set: bool


@dataclass(frozen=True)
class ChannelView:
    """A channel's configured state and its (masked) fields."""

    name: str
    configured: bool
    fields: list[ChannelFieldView]


async def channel_views(session: AsyncSession, base: Settings) -> list[ChannelView]:
    """Return the effective, masked channel configuration for the UI."""
    effective = await resolve_channel_settings(session, base)
    views: list[ChannelView] = []
    for name, fields in CHANNEL_FIELDS.items():
        field_views: list[ChannelFieldView] = []
        for field in fields:
            raw = getattr(effective, field)
            value = "" if raw is None else str(raw)
            is_secret = field in SECRET_FIELDS
            field_views.append(
                ChannelFieldView(
                    name=field,
                    value=_mask(value) if (is_secret and value) else value,
                    is_secret=is_secret,
                    is_set=bool(value),
                )
            )
        views.append(
            ChannelView(
                name=name,
                configured=_channel_configured(name, effective),
                fields=field_views,
            )
        )
    return views


async def save_channel_config(
    session: AsyncSession, name: str, fields: Mapping[str, str | None]
) -> None:
    """Persist channel fields: None leaves a field unchanged, "" clears it.

    Clearing removes the DB override so the value falls back to the environment.

    Raises:
        ValueError: if the channel name is unknown.
    """
    valid = CHANNEL_FIELDS.get(name)
    if valid is None:
        raise ValueError(f"unknown channel: {name}")
    now = datetime.now(UTC)
    for field, value in fields.items():
        if field not in valid or value is None:
            continue
        key = KEY_PREFIX + field
        existing = await session.get(models.AppSetting, key)
        if value == "":
            if existing is not None:
                await session.delete(existing)
        elif existing is None:
            session.add(models.AppSetting(key=key, value=value, updated_at=now))
        else:
            existing.value = value
            existing.updated_at = now
