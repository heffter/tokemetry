"""Unit tests for the Z.ai coding-plan limits source (Task 69.4)."""

import json
from pathlib import Path

import httpx
import pytest
from tokemetry_collector.config import CollectorConfig
from tokemetry_collector.limits_zai import ZaiCodingLimitsSource, read_zai_auth
from tokemetry_collector.sources import build_limit_sources
from tokemetry_core.interfaces import LimitsUnavailableError

_KEY = "zai-secret-key"
_QUOTA_PAYLOAD = {
    "account": "zai-acct",
    "quota": {
        "prompt_5h": {
            "used_percent": 73.0,
            "limit": 500,
            "remaining": 135,
            "resets_at": "2026-07-09T15:00:00+00:00",
        }
    },
}


def _write_config(home: Path, key: str = _KEY, account: str = "zai-acct") -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.json").write_text(
        json.dumps({"api_key": key, "account": account}), encoding="utf-8"
    )


def _source(home: Path, handler: object) -> ZaiCodingLimitsSource:
    client = httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    return ZaiCodingLimitsSource(zai_home=home, machine="box-1", client=client)


class TestReadAuth:
    def test_reads_key_and_account(self, tmp_path: Path) -> None:
        _write_config(tmp_path, "k-1", "acct-1")
        assert read_zai_auth(tmp_path) == ("k-1", "acct-1")

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert read_zai_auth(tmp_path) is None

    def test_no_key_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "config.json").write_text(json.dumps({"account": "a"}), "utf-8")
        assert read_zai_auth(tmp_path) is None


class TestPoll:
    def test_maps_quota_window(self, tmp_path: Path) -> None:
        _write_config(tmp_path)

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/usage")
            assert request.headers["authorization"] == f"Bearer {_KEY}"
            return httpx.Response(200, json=_QUOTA_PAYLOAD)

        snapshots = _source(tmp_path, handler).poll()
        assert len(snapshots) == 1
        snap = snapshots[0]
        assert snap.window_kind == "prompt_5h"
        assert snap.utilization_pct == 73.0
        assert snap.provider == "zai"
        assert snap.resets_at is not None
        assert snap.raw["account"] == "zai-acct"

    def test_quota_exhausted_maps_to_full_utilization(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        payload = {
            "quota": {"prompt_5h": {"used_percent": 100.0, "remaining": 0}}
        }

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        snapshots = _source(tmp_path, handler).poll()
        assert snapshots[0].utilization_pct == 100.0

    def test_missing_credential_raises_unavailable(self, tmp_path: Path) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_QUOTA_PAYLOAD)

        with pytest.raises(LimitsUnavailableError):
            _source(tmp_path, handler).poll()

    def test_auth_rejected_raises_unavailable(self, tmp_path: Path) -> None:
        _write_config(tmp_path)

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"error": "forbidden"})

        with pytest.raises(LimitsUnavailableError):
            _source(tmp_path, handler).poll()

    def test_malformed_response_raises_unavailable(self, tmp_path: Path) -> None:
        _write_config(tmp_path)

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"unexpected": True})

        with pytest.raises(LimitsUnavailableError):
            _source(tmp_path, handler).poll()

    def test_network_error_raises_unavailable(self, tmp_path: Path) -> None:
        _write_config(tmp_path)

        def handler(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        with pytest.raises(LimitsUnavailableError):
            _source(tmp_path, handler).poll()

    def test_no_key_leak_in_snapshots(self, tmp_path: Path) -> None:
        _write_config(tmp_path)

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_QUOTA_PAYLOAD)

        for snapshot in _source(tmp_path, handler).poll():
            assert _KEY not in json.dumps(snapshot.model_dump(mode="json"))


def test_builder_registered_and_off_by_default(tmp_path: Path) -> None:
    disabled = CollectorConfig.model_validate(
        {"server_url": "http://s", "api_token": "t", "machine_name": "m", "limits": {}}
    )
    assert build_limit_sources(disabled) == []

    enabled = CollectorConfig.model_validate(
        {
            "server_url": "http://s",
            "api_token": "t",
            "machine_name": "m",
            "limits": {"zai_coding_plan": {"enabled": True, "zai_home": str(tmp_path)}},
        }
    )
    sources = build_limit_sources(enabled)
    assert len(sources) == 1
    assert isinstance(sources[0], ZaiCodingLimitsSource)


def test_all_three_limit_sources_build_together(tmp_path: Path) -> None:
    # Config round-trip enabling anthropic_oauth, openai_codex, and
    # zai_coding_plan at once (they coexist, no key collisions).
    config = CollectorConfig.model_validate(
        {
            "server_url": "http://s",
            "api_token": "t",
            "machine_name": "m",
            "limits": {
                "anthropic_oauth": {"enabled": True, "claude_home": str(tmp_path / "c")},
                "openai_codex": {"enabled": True, "codex_home": str(tmp_path / "o")},
                "zai_coding_plan": {"enabled": True, "zai_home": str(tmp_path / "z")},
            },
        }
    )
    providers = {source.provider for source in build_limit_sources(config)}
    assert providers == {"anthropic", "openai", "zai"}
