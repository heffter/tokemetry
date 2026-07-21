"""Unit tests for the HTTP uploader."""

import httpx
from tokemetry_collector.uploader import Uploader


def _uploader(handler: object) -> Uploader:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    client = httpx.Client(transport=transport)
    return Uploader("http://server", "tkm_token", client=client)


def test_send_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/ingest/events"
        assert request.headers["authorization"] == "Bearer tkm_token"
        return httpx.Response(200, json={"accepted": 1})

    uploader = _uploader(handler)
    assert uploader.send("events", {"machine": {}, "events": []}) is True


def test_send_rejected_status_returns_false() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "bad"})

    assert _uploader(handler).send("events", {}) is False


def test_send_network_error_returns_false() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    assert _uploader(handler).send("events", {}) is False


def test_endpoint_paths() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200)

    uploader = _uploader(handler)
    uploader.send("events", {})
    uploader.send("limits", {})
    uploader.send("bootstrap", {})
    assert seen == [
        "/api/v1/ingest/events",
        "/api/v2/ingest/limits",
        "/api/v1/ingest/bootstrap",
    ]
