"""In-process pub/sub for the live dashboard WebSocket stream.

A single-process broadcaster: subscribers each get a bounded asyncio queue,
and publishers fan out a message to all of them. Slow or dead subscribers
that fill their queue are dropped rather than blocking ingest -- the stream
is a best-effort live view, not a durable log.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

#: Max buffered messages per subscriber before it is considered too slow.
_QUEUE_SIZE = 100


class Broadcaster:
    """Fans out JSON-serializable messages to WebSocket subscribers."""

    def __init__(self) -> None:
        """Create a broadcaster with no subscribers."""
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    async def publish(self, message: dict[str, Any]) -> None:
        """Deliver ``message`` to every subscriber, dropping full queues."""
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                self._subscribers.discard(queue)

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        """Register a subscriber queue for the duration of the context."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_QUEUE_SIZE)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        """Number of currently connected subscribers."""
        return len(self._subscribers)
