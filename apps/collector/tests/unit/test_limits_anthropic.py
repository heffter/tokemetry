"""Unit tests for the Anthropic OAuth limits source."""

import json
from pathlib import Path

import httpx
import pytest
from tokemetry_collector.config import CollectorConfig
from tokemetry_collector.limits_anthropic import (
    AnthropicOAuthLimitsSource,
    read_oauth_token,
)
from tokemetry_collector.sources import build_limit_sources
from tokemetry_core.interfaces import LimitsUnavailableError

_USAGE_PAYLOAD = {
    "five_hour": {"utilization": 42.5, "resets_at": "2026-07-09T13:00:00+00:00"},
    "seven_day": {"utilization": 15.0, "resets_at": "2026-07-14T00:00:00+00:00"},
    "seven_day_opus": {"utilization": 60.0, "resets_at": 1_784_000_000},
    "seven_day_sonnet": None,
}


def _write_credentials(home: Path, token: str = "oauth-abc") -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": token, "refreshToken": "r"}}),
        encoding="utf-8",
    )


def _source(home: Path, handler: object) -> AnthropicOAuthLimitsSource:
    client = httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    return AnthropicOAuthLimitsSource(claude_home=home, machine="box-1", client=client)


class TestReadToken:
    def test_reads_nested_access_token(self, tmp_path: Path) -> None:
        _write_credentials(tmp_path, "tok-123")
        assert read_oauth_token(tmp_path) == "tok-123"

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert read_oauth_token(tmp_path) is None

    def test_corrupt_file_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / ".credentials.json").write_text("{broken", encoding="utf-8")
        assert read_oauth_token(tmp_path) is None


class TestPoll:
    def test_maps_windows_to_snapshots(self, tmp_path: Path) -> None:
        _write_credentials(tmp_path)

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/oauth/usage"
            assert request.headers["authorization"] == "Bearer oauth-abc"
            assert request.headers["anthropic-beta"] == "oauth-2025-04-20"
            return httpx.Response(200, json=_USAGE_PAYLOAD)

        snapshots = _source(tmp_path, handler).poll()

        kinds = {snapshot.window_kind: snapshot for snapshot in snapshots}
        assert set(kinds) == {"five_hour", "seven_day", "seven_day_opus"}
        assert kinds["five_hour"].utilization_pct == 42.5
        assert kinds["five_hour"].resets_at is not None
        assert kinds["seven_day_opus"].resets_at is not None  # epoch parsed
        assert all(s.provider == "anthropic" for s in snapshots)

    def test_missing_token_raises_unavailable(self, tmp_path: Path) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_USAGE_PAYLOAD)

        with pytest.raises(LimitsUnavailableError):
            _source(tmp_path, handler).poll()

    def test_error_status_raises_unavailable(self, tmp_path: Path) -> None:
        _write_credentials(tmp_path)

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "unauthorized"})

        with pytest.raises(LimitsUnavailableError):
            _source(tmp_path, handler).poll()

    def test_network_error_raises_unavailable(self, tmp_path: Path) -> None:
        _write_credentials(tmp_path)

        def handler(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        with pytest.raises(LimitsUnavailableError):
            _source(tmp_path, handler).poll()

    def test_empty_windows_returns_empty_list(self, tmp_path: Path) -> None:
        _write_credentials(tmp_path)

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"unrelated": True})

        assert _source(tmp_path, handler).poll() == []


def test_builder_registered(tmp_path: Path) -> None:
    config = CollectorConfig.model_validate(
        {
            "server_url": "http://s",
            "api_token": "t",
            "machine_name": "m",
            "limits": {"anthropic_oauth": {"enabled": True, "claude_home": str(tmp_path)}},
        }
    )
    sources = build_limit_sources(config)
    assert len(sources) == 1
    assert isinstance(sources[0], AnthropicOAuthLimitsSource)
