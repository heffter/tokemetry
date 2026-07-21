"""OpenAI/Codex subscription limits source.

Reads the access token the Codex CLI stores locally (``auth.json`` under the
Codex home) and queries the undocumented usage/rate-limit endpoint the Codex
client uses for the primary and secondary subscription windows. The token never
leaves the machine -- only the resulting utilization percentages (and the local
account label) are uploaded.

Like the Anthropic source, this endpoint is unofficial and may change: every
failure mode (missing credentials, expired auth, endpoint change, network
error, unparseable body) is surfaced as :class:`LimitsUnavailableError`, so the
collector loop degrades gracefully (the server records a limit_source_failure
data-quality event) instead of crashing. All endpoint specifics are isolated in
this one module so drift is a one-file fix (R-008 pattern).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from loguru import logger
from tokemetry_core.interfaces import LimitsSource, LimitsUnavailableError
from tokemetry_core.models import LimitSnapshot, Provenance

#: Base URL of the Codex/ChatGPT backend the CLI talks to.
CODEX_API_BASE = "https://chatgpt.com/backend-api"

#: Undocumented usage/rate-limit endpoint path the Codex client uses.
CODEX_USAGE_PATH = "/codex/usage"

#: A Codex-CLI-like User-Agent to avoid aggressive rate limiting.
_USER_AGENT = "codex-cli/0.1 (external, tokemetry-collector)"

#: The window keys the response is expected to expose, in display order.
_WINDOW_KEYS = ("primary", "secondary")


def default_codex_home() -> Path:
    """Resolve the Codex home directory (``$CODEX_HOME`` or ``~/.codex``)."""
    override = os.environ.get("CODEX_HOME")
    return Path(override) if override else Path.home() / ".codex"


def read_codex_auth(codex_home: Path) -> tuple[str, str | None] | None:
    """Return ``(access_token, account_id)`` from ``auth.json``, or None.

    The file schema is not officially documented, so the access token and
    account id are located by a defensive recursive search.
    """
    path = codex_home / "auth.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    token = _find_first(data, ("access_token", "accessToken"))
    if not token:
        return None
    account = _find_first(data, ("account_id", "accountId", "account"))
    return token, account


def _find_first(node: Any, keys: tuple[str, ...]) -> str | None:
    """Recursively search a decoded JSON structure for the first matching key."""
    if isinstance(node, dict):
        for key in keys:
            value = node.get(key)
            if isinstance(value, str) and value:
                return value
        for value in node.values():
            found = _find_first(value, keys)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_first(item, keys)
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


class OpenAICodexLimitsSource(LimitsSource):
    """Polls the Codex usage endpoint for subscription-window utilization."""

    provider = "openai"

    def __init__(
        self,
        codex_home: Path,
        machine: str | None = None,
        client: httpx.Client | None = None,
        base_url: str = CODEX_API_BASE,
        timeout: float = 15.0,
    ) -> None:
        """Create the source.

        Args:
            codex_home: Directory holding ``auth.json``.
            machine: Machine name (the collector stamps it on the batch).
            client: Optional injected HTTP client (for tests).
            base_url: API base URL.
            timeout: Request timeout in seconds.
        """
        self._codex_home = codex_home
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
        auth = read_codex_auth(self._codex_home)
        if auth is None:
            raise LimitsUnavailableError("no access token in auth.json")
        token, account = auth

        try:
            response = self._client.get(
                self._base_url + CODEX_USAGE_PATH,
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": _USER_AGENT,
                },
            )
        except httpx.HTTPError as exc:
            raise LimitsUnavailableError(f"request failed: {exc}") from exc

        if response.status_code in (401, 403):
            raise LimitsUnavailableError(f"auth rejected (HTTP {response.status_code})")
        if not response.is_success:
            raise LimitsUnavailableError(f"HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise LimitsUnavailableError("unparseable response body") from exc

        return self._to_snapshots(payload, account)

    def _to_snapshots(
        self, payload: dict[str, Any], account: str | None
    ) -> list[LimitSnapshot]:
        """Map a Codex usage payload into normalized limit snapshots."""
        if not isinstance(payload, dict):
            raise LimitsUnavailableError("unexpected response shape")
        windows = payload.get("rate_limits")
        if not isinstance(windows, dict):
            raise LimitsUnavailableError("no rate_limits in response")
        now = datetime.now(UTC)
        snapshots: list[LimitSnapshot] = []
        for key in _WINDOW_KEYS:
            window = windows.get(key)
            if not isinstance(window, dict):
                continue
            used = window.get("used_percent")
            if not isinstance(used, int | float):
                continue
            # The account label now lands in the dedicated column via the v2
            # limits upload (Task 76); raw keeps the untouched window payload.
            snapshots.append(
                LimitSnapshot(
                    provider=self.provider,
                    ts=now,
                    machine=self._machine,
                    window_kind=key,
                    utilization_pct=float(used),
                    resets_at=_parse_reset(window.get("resets_at")),
                    provenance=Provenance.OFFICIAL,
                    account=account,
                    raw=dict(window),
                )
            )
        if not snapshots:
            logger.info("Codex usage response contained no recognized limit windows")
        return snapshots
