"""Notification channels: ntfy, Telegram, and SMTP email.

Each notifier is constructed from server settings, reports whether it is
configured, and sends a title/body. Connection secrets live in settings, so
alert rows in the database only reference channels by name. HTTP notifiers
use an injected async client (mockable); SMTP runs in a worker thread.
"""

from __future__ import annotations

import abc
import asyncio
import smtplib
from email.message import EmailMessage

import httpx
from loguru import logger

from tokemetry_server.config import Settings


class Notifier(abc.ABC):
    """A notification channel."""

    #: Channel name referenced by alert rules.
    name: str

    @abc.abstractmethod
    def is_configured(self) -> bool:
        """True when the channel has everything it needs to send."""

    @abc.abstractmethod
    async def send(self, title: str, body: str) -> bool:
        """Send a notification; return True on success."""


class NtfyNotifier(Notifier):
    """Publishes to an ntfy topic over HTTP."""

    name = "ntfy"

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        """Create the notifier from settings and an async client."""
        self._settings = settings
        self._client = client

    def is_configured(self) -> bool:
        """True when an ntfy topic is set."""
        return bool(self._settings.ntfy_topic)

    async def send(self, title: str, body: str) -> bool:
        """POST the message to the configured ntfy topic."""
        if not self.is_configured():
            return False
        url = f"{self._settings.ntfy_url.rstrip('/')}/{self._settings.ntfy_topic}"
        try:
            response = await self._client.post(
                url, content=body.encode("utf-8"), headers={"Title": title}
            )
            return response.is_success
        except httpx.HTTPError as exc:
            logger.warning("ntfy send failed: {}", exc)
            return False


class TelegramNotifier(Notifier):
    """Sends a message via the Telegram Bot API."""

    name = "telegram"

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        """Create the notifier from settings and an async client."""
        self._settings = settings
        self._client = client

    def is_configured(self) -> bool:
        """True when a bot token and chat id are set."""
        return bool(self._settings.telegram_bot_token and self._settings.telegram_chat_id)

    async def send(self, title: str, body: str) -> bool:
        """Send the message to the configured chat."""
        if not self.is_configured():
            return False
        url = f"https://api.telegram.org/bot{self._settings.telegram_bot_token}/sendMessage"
        payload = {
            "chat_id": self._settings.telegram_chat_id,
            "text": f"*{title}*\n{body}",
            "parse_mode": "Markdown",
        }
        try:
            response = await self._client.post(url, json=payload)
            return response.is_success
        except httpx.HTTPError as exc:
            logger.warning("telegram send failed: {}", exc)
            return False


class SmtpNotifier(Notifier):
    """Sends email via SMTP (run in a worker thread to stay non-blocking)."""

    name = "smtp"

    def __init__(self, settings: Settings) -> None:
        """Create the notifier from settings."""
        self._settings = settings

    def is_configured(self) -> bool:
        """True when host, from, and to are all set."""
        s = self._settings
        return bool(s.smtp_host and s.smtp_from and s.smtp_to)

    async def send(self, title: str, body: str) -> bool:
        """Send the email off the event loop."""
        if not self.is_configured():
            return False
        return await asyncio.to_thread(self._send_sync, title, body)

    def _send_sync(self, title: str, body: str) -> bool:
        """Blocking SMTP send."""
        s = self._settings
        message = EmailMessage()
        message["Subject"] = title
        message["From"] = s.smtp_from or ""
        message["To"] = s.smtp_to or ""
        message.set_content(body)
        try:
            with smtplib.SMTP(s.smtp_host or "", s.smtp_port, timeout=15) as server:
                if s.smtp_use_tls:
                    server.starttls()
                if s.smtp_user and s.smtp_password:
                    server.login(s.smtp_user, s.smtp_password)
                server.send_message(message)
            return True
        except (OSError, smtplib.SMTPException) as exc:
            logger.warning("smtp send failed: {}", exc)
            return False


def build_notifiers(settings: Settings, client: httpx.AsyncClient) -> dict[str, Notifier]:
    """Build the notifier registry keyed by channel name."""
    notifiers: list[Notifier] = [
        NtfyNotifier(settings, client),
        TelegramNotifier(settings, client),
        SmtpNotifier(settings),
    ]
    return {notifier.name: notifier for notifier in notifiers}
