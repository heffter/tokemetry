"""Replay driver posting fixture batches through the real HTTP ingest surface.

The driver mints an ingest-only token and drives the *actual* Python client
(``tokemetry_client.IngestClient``) against the in-process app -- the client's
httpx transport is the test's ``TestClient``, so events traverse the real
``POST /api/v2/ingest/events`` route exactly as a proxy exporter would. This
exercises the client end to end and lets the harness assert the server's ingest
counts and replay safety (Task 65.4).
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from tokemetry_client import IngestClient, IngestResult, UsageEventV2
from tokemetry_server.scopes import INGEST_EVENTS

# The client's server_url only needs to match the TestClient's base host; the
# ASGI transport routes to the app regardless of host.
_SERVER_URL = "http://testserver"


def mint_ingest_token(client: TestClient, auth: dict[str, str]) -> str:
    """Create an ingest-only token via the admin API and return its secret."""
    response = client.post(
        "/api/v1/tokens",
        json={"label": "proxy-harness", "scopes": [INGEST_EVENTS]},
        headers=auth,
    )
    assert response.status_code == 201, response.text
    token: str = response.json()["token"]
    return token


class ReplayDriver:
    """Posts fixture batches through the Python client to the in-process app."""

    def __init__(self, client: TestClient, token: str) -> None:
        # A batch size above any fixture keeps each scenario in a single POST so
        # snapshot ordering is preserved and accepted counts are unambiguous.
        self._ingest = IngestClient(
            _SERVER_URL,
            token,
            client=client,  # TestClient is an httpx.Client over the ASGI app.
            batch_size=1000,
        )

    def replay(self, events: list[dict[str, Any]]) -> IngestResult:
        """Validate the wire dicts and ingest them through the client."""
        models = [UsageEventV2.model_validate(event) for event in events]
        return self._ingest.ingest(models)


def post_batch(
    client: TestClient,
    token: str,
    events: list[dict[str, Any]],
    *,
    correction: bool = False,
) -> dict[str, Any]:
    """POST a batch directly and return the full ingest response body.

    Used where the harness needs the server's per-batch count breakdown
    (accepted/updated/duplicate/rejected), which the client does not surface.
    """
    response = client.post(
        "/api/v2/ingest/events",
        json={"schema_version": 2, "events": events, "correction": correction},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body
