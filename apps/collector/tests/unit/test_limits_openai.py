"""Unit tests for the OpenAI/Codex limits source (Task 69.3)."""

import json
from pathlib import Path

import httpx
import pytest
from tokemetry_collector.config import CollectorConfig
from tokemetry_collector.limits_openai import (
    OpenAICodexLimitsSource,
    read_codex_auth,
)
from tokemetry_collector.sources import build_limit_sources
from tokemetry_core.interfaces import LimitsUnavailableError

_TOKEN = "codex-secret-token"
_USAGE_PAYLOAD = {
    "account_id": "acct_xyz",
    "rate_limits": {
        "primary": {"used_percent": 42.5, "resets_at": "2026-07-09T13:00:00+00:00"},
        "secondary": {"used_percent": 88.0, "resets_at": 1_784_000_000},
    },
}


def _write_auth(home: Path, token: str = _TOKEN, account: str = "acct_xyz") -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "auth.json").write_text(
        json.dumps({"tokens": {"access_token": token}, "account_id": account}),
        encoding="utf-8",
    )


def _source(home: Path, handler: object) -> OpenAICodexLimitsSource:
    client = httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    return OpenAICodexLimitsSource(codex_home=home, machine="box-1", client=client)


class TestReadAuth:
    def test_reads_token_and_account(self, tmp_path: Path) -> None:
        _write_auth(tmp_path, "tok-123", "acct-1")
        assert read_codex_auth(tmp_path) == ("tok-123", "acct-1")

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert read_codex_auth(tmp_path) is None

    def test_corrupt_file_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "auth.json").write_text("{broken", encoding="utf-8")
        assert read_codex_auth(tmp_path) is None

    def test_no_token_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "auth.json").write_text(json.dumps({"account_id": "a"}), "utf-8")
        assert read_codex_auth(tmp_path) is None


class TestPoll:
    def test_maps_primary_and_secondary_windows(self, tmp_path: Path) -> None:
        _write_auth(tmp_path)

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/codex/usage")
            assert request.headers["authorization"] == f"Bearer {_TOKEN}"
            return httpx.Response(200, json=_USAGE_PAYLOAD)

        snapshots = _source(tmp_path, handler).poll()
        kinds = {s.window_kind: s for s in snapshots}
        assert set(kinds) == {"primary", "secondary"}
        assert kinds["primary"].utilization_pct == 42.5
        assert kinds["primary"].resets_at is not None
        assert kinds["secondary"].resets_at is not None  # epoch parsed
        assert all(s.provider == "openai" for s in snapshots)
        # The local account label rides in raw.
        assert kinds["primary"].raw["account"] == "acct_xyz"

    def test_missing_credentials_raises_unavailable(self, tmp_path: Path) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_USAGE_PAYLOAD)

        with pytest.raises(LimitsUnavailableError):
            _source(tmp_path, handler).poll()

    def test_expired_auth_raises_unavailable(self, tmp_path: Path) -> None:
        _write_auth(tmp_path)

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "expired"})

        with pytest.raises(LimitsUnavailableError):
            _source(tmp_path, handler).poll()

    def test_malformed_response_raises_unavailable(self, tmp_path: Path) -> None:
        _write_auth(tmp_path)

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"unexpected": True})

        with pytest.raises(LimitsUnavailableError):
            _source(tmp_path, handler).poll()

    def test_unparseable_body_raises_unavailable(self, tmp_path: Path) -> None:
        _write_auth(tmp_path)

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"not json")

        with pytest.raises(LimitsUnavailableError):
            _source(tmp_path, handler).poll()

    def test_network_error_raises_unavailable(self, tmp_path: Path) -> None:
        _write_auth(tmp_path)

        def handler(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        with pytest.raises(LimitsUnavailableError):
            _source(tmp_path, handler).poll()

    def test_no_token_leak_in_snapshots(self, tmp_path: Path) -> None:
        # The uploaded snapshot (fields + raw) must never carry the access token.
        _write_auth(tmp_path)

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_USAGE_PAYLOAD)

        snapshots = _source(tmp_path, handler).poll()
        assert snapshots
        for snapshot in snapshots:
            assert _TOKEN not in json.dumps(snapshot.model_dump(mode="json"))


def test_builder_registered_and_off_by_default(tmp_path: Path) -> None:
    # Registered but only built when enabled in config.
    disabled = CollectorConfig.model_validate(
        {"server_url": "http://s", "api_token": "t", "machine_name": "m", "limits": {}}
    )
    assert build_limit_sources(disabled) == []

    enabled = CollectorConfig.model_validate(
        {
            "server_url": "http://s",
            "api_token": "t",
            "machine_name": "m",
            "limits": {"openai_codex": {"enabled": True, "codex_home": str(tmp_path)}},
        }
    )
    sources = build_limit_sources(enabled)
    assert len(sources) == 1
    assert isinstance(sources[0], OpenAICodexLimitsSource)
