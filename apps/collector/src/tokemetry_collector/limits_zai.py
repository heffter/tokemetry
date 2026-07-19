"""Z.ai GLM coding-plan limits source.

Reads the Z.ai API key the coding-plan client stores locally (``config.json``
under the Z.ai home) and queries the undocumented coding-plan quota endpoint for
the plan's quota windows (for example a prompt-count-per-five-hours window). The
key never leaves the machine -- only the resulting utilization percentages (and
the local account label) are uploaded.

Like the other provider limit sources, this endpoint is unofficial and may
change: every failure (missing credential, quota-endpoint change, auth
rejection, network error, unparseable body) is surfaced as
:class:`LimitsUnavailableError` so the collector loop degrades gracefully (the
server records a limit_source_failure data-quality event). All endpoint
specifics are isolated in this one module so drift is a one-file fix (R-008).
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

#: Base URL of the Z.ai coding-plan API.
ZAI_API_BASE = "https://api.z.ai"

#: Undocumented coding-plan quota endpoint path.
ZAI_QUOTA_PATH = "/api/coding/paas/v4/usage"

#: A Z.ai-client-like User-Agent to avoid aggressive rate limiting.
_USER_AGENT = "zai-coding/0.1 (external, tokemetry-collector)"

#: The quota window keys the response is expected to expose, in display order.
_WINDOW_KEYS = ("prompt_5h",)


def default_zai_home() -> Path:
    """Resolve the Z.ai home directory (``$ZAI_HOME`` or ``~/.zai``)."""
    override = os.environ.get("ZAI_HOME")
    return Path(override) if override else Path.home() / ".zai"


def read_zai_auth(zai_home: Path) -> tuple[str, str | None] | None:
    """Return ``(api_key, account)`` from ``config.json``, or None.

    The file schema is not officially documented, so the key and account are
    located by a defensive recursive search.
    """
    path = zai_home / "config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    key = _find_first(data, ("api_key", "apiKey", "token"))
    if not key:
        return None
    account = _find_first(data, ("account", "account_id", "email"))
    return key, account


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


class ZaiCodingLimitsSource(LimitsSource):
    """Polls the Z.ai coding-plan quota endpoint for window utilization."""

    provider = "zai"

    def __init__(
        self,
        zai_home: Path,
        machine: str | None = None,
        client: httpx.Client | None = None,
        base_url: str = ZAI_API_BASE,
        timeout: float = 15.0,
    ) -> None:
        """Create the source.

        Args:
            zai_home: Directory holding ``config.json``.
            machine: Machine name (the collector stamps it on the batch).
            client: Optional injected HTTP client (for tests).
            base_url: API base URL.
            timeout: Request timeout in seconds.
        """
        self._zai_home = zai_home
        self._machine = machine
        self._client = client if client is not None else httpx.Client(timeout=timeout)
        self._owns_client = client is None
        self._base_url = base_url.rstrip("/")

    def close(self) -> None:
        """Close the HTTP client if this source created it."""
        if self._owns_client:
            self._client.close()

    def poll(self) -> list[LimitSnapshot]:
        """Fetch current quota utilization; raise if unavailable."""
        auth = read_zai_auth(self._zai_home)
        if auth is None:
            raise LimitsUnavailableError("no api_key in config.json")
        key, account = auth

        try:
            response = self._client.get(
                self._base_url + ZAI_QUOTA_PATH,
                headers={
                    "Authorization": f"Bearer {key}",
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
        """Map a Z.ai quota payload into normalized limit snapshots."""
        if not isinstance(payload, dict):
            raise LimitsUnavailableError("unexpected response shape")
        windows = payload.get("quota")
        if not isinstance(windows, dict):
            raise LimitsUnavailableError("no quota in response")
        now = datetime.now(UTC)
        snapshots: list[LimitSnapshot] = []
        for key in _WINDOW_KEYS:
            window = windows.get(key)
            if not isinstance(window, dict):
                continue
            used = window.get("used_percent")
            if not isinstance(used, int | float):
                continue
            # Account label and quota amounts ride in raw until the collector
            # adopts the v2 limits upload (LimitSnapshot has no such fields).
            raw = dict(window)
            if account is not None:
                raw["account"] = account
            snapshots.append(
                LimitSnapshot(
                    provider=self.provider,
                    ts=now,
                    machine=self._machine,
                    window_kind=key,
                    utilization_pct=float(used),
                    resets_at=_parse_reset(window.get("resets_at")),
                    provenance=Provenance.OFFICIAL,
                    raw=raw,
                )
            )
        if not snapshots:
            logger.info("Z.ai quota response contained no recognized windows")
        return snapshots
