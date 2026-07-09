"""Unit tests for notification channels."""

import httpx
from tokemetry_server.config import Settings
from tokemetry_server.services.alerting.notifiers import (
    NtfyNotifier,
    SmtpNotifier,
    TelegramNotifier,
    build_notifiers,
)


def _client(handler: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


async def test_ntfy_unconfigured_does_not_send() -> None:
    notifier = NtfyNotifier(Settings(), _client(lambda r: httpx.Response(200)))
    assert notifier.is_configured() is False
    assert await notifier.send("t", "b") is False


async def test_ntfy_sends_to_topic() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["title"] = request.headers.get("Title", "")
        return httpx.Response(200)

    settings = Settings(ntfy_url="https://ntfy.example", ntfy_topic="tok")
    notifier = NtfyNotifier(settings, _client(handler))

    assert await notifier.send("Alert", "body") is True
    assert seen["url"] == "https://ntfy.example/tok"
    assert seen["title"] == "Alert"


async def test_telegram_sends_when_configured() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "sendMessage" in str(request.url)
        return httpx.Response(200, json={"ok": True})

    settings = Settings(telegram_bot_token="123:abc", telegram_chat_id="42")
    notifier = TelegramNotifier(settings, _client(handler))

    assert notifier.is_configured() is True
    assert await notifier.send("t", "b") is True


def test_smtp_configured_flag() -> None:
    assert SmtpNotifier(Settings()).is_configured() is False
    configured = SmtpNotifier(
        Settings(smtp_host="mail", smtp_from="a@b.c", smtp_to="d@e.f")
    )
    assert configured.is_configured() is True


def test_build_notifiers_registry() -> None:
    registry = build_notifiers(Settings(), _client(lambda r: httpx.Response(200)))
    assert set(registry) == {"ntfy", "telegram", "smtp"}
