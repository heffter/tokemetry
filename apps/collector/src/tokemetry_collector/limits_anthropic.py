"""Anthropic subscription limits source (OAuth usage endpoint).

Reads the OAuth access token Claude Code stores locally and queries the
undocumented ``/api/oauth/usage`` endpoint for authoritative, cross-device
5-hour and weekly limit utilization. The token never leaves the machine --
only the resulting utilization percentages are uploaded.

This endpoint is unofficial and may change or disappear; every failure mode
(missing token, network error, non-2xx, unparseable body) is surfaced as
:class:`LimitsUnavailableError` so the collector degrades to local
estimates instead of crashing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from loguru import logger
from tokemetry_core.interfaces import LimitsSource, LimitsUnavailableError
from tokemetry_core.models import LimitSnapshot, Provenance

#: Base URL of the Anthropic API.
ANTHROPIC_API_BASE = "https://api.anthropic.com"

#: Undocumented OAuth usage endpoint path.
OAUTH_USAGE_PATH = "/api/oauth/usage"

#: Required beta header for the OAuth usage endpoint.
_OAUTH_BETA = "oauth-2025-04-20"

#: Claude-Code-like User-Agent to avoid aggressive rate limiting.
_USER_AGENT = "claude-cli/2.1 (external, tokemetry-collector)"

#: Response keys that describe a limit window (others are ignored).
_WINDOW_KEYS = ("five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet")


def read_oauth_token(claude_home: Path) -> str | None:
    """Return the OAuth access token from ``.credentials.json``, or None.

    The file schema is not officially documented, so the access token is
    located by a defensive recursive search for an ``accessToken`` key.
    """
    path = claude_home / ".credentials.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return _find_access_token(data)


def _find_access_token(node: Any) -> str | None:
    """Recursively search a decoded JSON structure for an access token."""
    if isinstance(node, dict):
        for key in ("accessToken", "access_token"):
            value = node.get(key)
            if isinstance(value, str) and value:
                return value
        for value in node.values():
            found = _find_access_token(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_access_token(item)
            if found is not None:
                return found
    return None


def _parse_reset(value: Any) -> datetime | None:
    """Parse a reset time given as ISO string or epoch seconds."""
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), tz=UTC)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


class AnthropicOAuthLimitsSource(LimitsSource):
    """Polls Anthropic's OAuth usage endpoint for limit utilization."""

    provider = "anthropic"

    def __init__(
        self,
        claude_home: Path,
        machine: str | None = None,
        client: httpx.Client | None = None,
        base_url: str = ANTHROPIC_API_BASE,
        timeout: float = 15.0,
    ) -> None:
        """Create the source.

        Args:
            claude_home: Directory holding ``.credentials.json``.
            machine: Machine name (unused for the snapshot; the collector
                stamps it on the batch).
            client: Optional injected HTTP client (for tests).
            base_url: API base URL.
            timeout: Request timeout in seconds.
        """
        self._claude_home = claude_home
        self._machine = machine
        self._client = client if client is not None else httpx.Client(timeout=timeout)
        self._owns_client = client is None
        self._base_url = base_url.rstrip("/")

    def close(self) -> None:
        """Close the HTTP client if this source created it."""
        if self._owns_client:
            self._client.close()

    def poll(self) -> list[LimitSnapshot]:
        """Fetch current limit utilization; raise if unavailable."""
        token = read_oauth_token(self._claude_home)
        if token is None:
            raise LimitsUnavailableError("no OAuth token in .credentials.json")

        try:
            response = self._client.get(
                self._base_url + OAUTH_USAGE_PATH,
                headers={
                    "Authorization": f"Bearer {token}",
                    "anthropic-beta": _OAUTH_BETA,
                    "User-Agent": _USER_AGENT,
                },
            )
        except httpx.HTTPError as exc:
            raise LimitsUnavailableError(f"request failed: {exc}") from exc

        if not response.is_success:
            raise LimitsUnavailableError(f"HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise LimitsUnavailableError("unparseable response body") from exc

        return self._to_snapshots(payload)

    def _to_snapshots(self, payload: dict[str, Any]) -> list[LimitSnapshot]:
        """Map an OAuth usage payload into normalized limit snapshots."""
        now = datetime.now(UTC)
        snapshots: list[LimitSnapshot] = []
        for key in _WINDOW_KEYS:
            window = payload.get(key)
            if not isinstance(window, dict):
                continue
            utilization = window.get("utilization")
            if not isinstance(utilization, int | float):
                continue
            snapshots.append(
                LimitSnapshot(
                    provider=self.provider,
                    ts=now,
                    machine=self._machine,
                    window_kind=key,
                    utilization_pct=float(utilization),
                    resets_at=_parse_reset(window.get("resets_at")),
                    provenance=Provenance.OFFICIAL,
                    raw=window,
                )
            )
        if not snapshots:
            logger.info("OAuth usage response contained no recognized limit windows")
        return snapshots
