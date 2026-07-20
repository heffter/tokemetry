"""Harness-local fixtures and import wiring for the proxy replay tests.

The Python ingest client (``tokemetry_client``) is a standalone package under
``packages/clients/python`` -- not a workspace member and not installed in the
server test environment. This harness is exactly the place the client is meant
to be driven against the server, so we put its ``src`` on ``sys.path`` here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# apps/server/tests/integration/proxy_harness/conftest.py -> repo root is 5 up.
_CLIENT_SRC = Path(__file__).resolve().parents[5] / "packages" / "clients" / "python" / "src"
if _CLIENT_SRC.is_dir() and str(_CLIENT_SRC) not in sys.path:
    sys.path.insert(0, str(_CLIENT_SRC))

from .driver import ReplayDriver, mint_ingest_token  # noqa: E402  (needs sys.path above)


@pytest.fixture
def ingest_token(client: TestClient, auth: dict[str, str]) -> str:
    """An ingest-only bearer token minted against the running app."""
    return mint_ingest_token(client, auth)


@pytest.fixture
def driver(client: TestClient, ingest_token: str) -> ReplayDriver:
    """A replay driver wired to drive the Python client against the app."""
    return ReplayDriver(client, ingest_token)
