"""Live event WebSocket stream for the dashboard.

Authenticated via a ``token`` query parameter (WebSocket handshakes cannot
carry an Authorization header in browsers). Subscribers receive JSON
messages published by ingest (for example a summary of each accepted event
batch).
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from starlette.applications import Starlette

from tokemetry_server.config import Settings
from tokemetry_server.db import models
from tokemetry_server.scopes import QUERY_READ
from tokemetry_server.security import hash_token

router = APIRouter()


async def _authorize(app: Starlette, token: str | None) -> bool:
    """Return True if ``token`` may read the stream (bootstrap or ``query:read``).

    The stream carries the same data as the REST query surface, so it requires
    the ``query:read`` scope to match (NFR-SEC-008).
    """
    if not token:
        return False
    settings: Settings = app.state.settings
    if settings.api_bootstrap_token and hmac.compare_digest(
        token, settings.api_bootstrap_token
    ):
        return True
    async with app.state.session_factory() as session:
        result = await session.execute(
            select(models.ApiToken).where(
                models.ApiToken.token_hash == hash_token(token),
                models.ApiToken.revoked.is_(False),
            )
        )
        row = result.scalar_one_or_none()
        return row is not None and QUERY_READ in (row.scopes or [])


@router.websocket("/api/v1/stream")
async def stream(websocket: WebSocket) -> None:
    """Stream live events to an authenticated dashboard client."""
    token = websocket.query_params.get("token")
    if not await _authorize(websocket.app, token):
        await websocket.close(code=1008)  # policy violation
        return

    await websocket.accept()
    broadcaster = websocket.app.state.broadcaster
    async with broadcaster.subscribe() as queue:
        try:
            while True:
                message = await queue.get()
                await websocket.send_json(message)
        except WebSocketDisconnect:
            return
