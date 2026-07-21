"""HTTP uploader for queued batches.

Thin wrapper over an ``httpx.Client`` that POSTs a queued batch to the right
ingest endpoint and reports success/failure as a boolean, so the runner can
decide whether to dequeue or retry. Network errors are treated as
retryable failures, never raised, keeping the daemon alive when the server
is unreachable.
"""

from __future__ import annotations

import httpx
from loguru import logger

#: Batch kind to ingest endpoint path. Limits use the v2 endpoint (Task 76) to
#: carry the account/organization/source/limit dimensions; the server keeps the
#: v1 limits endpoint for older collectors.
_ENDPOINTS = {
    "events": "/api/v1/ingest/events",
    "limits": "/api/v2/ingest/limits",
    "bootstrap": "/api/v1/ingest/bootstrap",
}


class Uploader:
    """Uploads batches to the tokemetry server ingest API."""

    def __init__(
        self,
        server_url: str,
        api_token: str,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        """Create the uploader.

        Args:
            server_url: Base URL of the server (WireGuard address).
            api_token: Bearer token for authentication.
            client: Optional injected client (for tests); one is created and
                owned when omitted.
            timeout: Per-request timeout in seconds.
        """
        self._base = server_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_token}"}
        self._client = client if client is not None else httpx.Client(timeout=timeout)
        self._owns_client = client is None

    def close(self) -> None:
        """Close the HTTP client if this uploader created it."""
        if self._owns_client:
            self._client.close()

    def send(self, kind: str, payload: dict[str, object]) -> bool:
        """POST a batch; return True on a 2xx response, False otherwise.

        Raises:
            KeyError: If ``kind`` is not a known ingest endpoint.
        """
        url = self._base + _ENDPOINTS[kind]
        try:
            response = self._client.post(url, json=payload, headers=self._headers)
        except httpx.HTTPError as exc:
            logger.warning("upload of {} batch failed (network): {}", kind, exc)
            return False
        if response.is_success:
            return True
        logger.warning("upload of {} batch rejected: HTTP {}", kind, response.status_code)
        return False
