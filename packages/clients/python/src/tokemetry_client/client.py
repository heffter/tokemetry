"""Hand-written ingest clients over the generated wire models (Task 65.3).

The Python analogue of the TypeScript client (D-012, companion FR-TOK-011..016):
a thin wrapper adding auth, batching, and a resilient submit policy -- retry
with full-jitter exponential backoff on 429/5xx, pause on 401 (auth broken, do
not hammer), and poison-event isolation on 400/422 by bisecting a rejected batch
so one malformed event never blocks the rest. Wire models come from
``tokemetry_client.models`` (generated from the OpenAPI spec).

Both a synchronous :class:`IngestClient` (httpx.Client) and an asynchronous
:class:`AsyncIngestClient` (httpx.AsyncClient) are provided with identical
semantics; the batching, backoff, and poison-classification logic is shared as
pure module-level helpers so the two clients differ only at their await points.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass, field
from enum import Enum, auto
from random import random

import httpx

from tokemetry_client.models import UsageEventV2

_DEFAULT_BATCH_SIZE = 100
_DEFAULT_MAX_BATCH_BYTES = 256 * 1024
_DEFAULT_MAX_RETRIES = 5
_DEFAULT_BACKOFF_BASE_MS = 200
_INGEST_PATH = "/api/v2/ingest/events"


class IngestAuthError(Exception):
    """Raised on a 401: the token is bad, so ingest pauses (no retry)."""


class IngestRetryError(Exception):
    """Raised when a batch still fails after exhausting 429/5xx retries."""

    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"ingest failed after retries (last status {status})")


@dataclass
class IngestResult:
    """Outcome of an ``ingest`` call (sync or async)."""

    accepted: int = 0
    rejected: int = 0
    batches: int = 0
    poison_events: list[UsageEventV2] = field(default_factory=list)


class _Disposition(Enum):
    """What to do with a batch given its POST response status."""

    ACCEPT = auto()
    AUTH_FAILED = auto()
    POISON = auto()
    BISECT = auto()
    RETRY_EXHAUSTED = auto()


def _classify(status: int, batch_len: int) -> _Disposition:
    """Map a (non-retryable) response status to a batch disposition.

    A single-event 400/422 is the poison itself; a multi-event 400/422 is
    bisected to find it. Called only after the retry loop has settled, so a
    429/5xx here means retries were exhausted.
    """
    if status < 300:
        return _Disposition.ACCEPT
    if status == 401:
        return _Disposition.AUTH_FAILED
    if status in (400, 422):
        return _Disposition.POISON if batch_len == 1 else _Disposition.BISECT
    return _Disposition.RETRY_EXHAUSTED


def _iter_batches(
    events: list[UsageEventV2], batch_size: int, max_batch_bytes: int
) -> Iterator[list[UsageEventV2]]:
    """Split events into batches bounded by count and serialized size."""
    current: list[UsageEventV2] = []
    size = 0
    for event in events:
        event_bytes = len(json.dumps(event.model_dump(mode="json"))) + 1
        if current and (
            len(current) >= batch_size or size + event_bytes > max_batch_bytes
        ):
            yield current
            current, size = [], 0
        current.append(event)
        size += event_bytes
    if current:
        yield current


def _payload(batch: list[UsageEventV2]) -> dict[str, object]:
    """Build the request envelope for a batch."""
    return {
        "schema_version": 2,
        "events": [event.model_dump(mode="json") for event in batch],
    }


def _backoff_ms(random_fn: Callable[[], float], base_ms: int, attempt: int) -> float:
    """Full-jitter backoff in ms: random in [0, base * 2**attempt]."""
    return float(random_fn() * base_ms * (2**attempt))


def _accepted_count(response: httpx.Response, batch_len: int) -> int:
    """Read the server's accepted count, falling back to the batch length."""
    try:
        body = response.json()
    except ValueError:
        return batch_len
    if isinstance(body, dict) and isinstance(body.get("accepted"), int):
        accepted: int = body["accepted"]
        return accepted
    return batch_len


class _BaseIngestClient:
    """Shared configuration for the sync and async ingest clients."""

    def __init__(
        self,
        server_url: str,
        token: str,
        *,
        batch_size: int,
        max_batch_bytes: int,
        max_retries: int,
        backoff_base_ms: int,
        random_fn: Callable[[], float],
    ) -> None:
        self._url = server_url.rstrip("/") + _INGEST_PATH
        self._token = token
        self._batch_size = batch_size
        self._max_batch_bytes = max_batch_bytes
        self._max_retries = max_retries
        self._backoff_base_ms = backoff_base_ms
        self._random = random_fn

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _split(self, events: Sequence[UsageEventV2]) -> Iterator[list[UsageEventV2]]:
        return _iter_batches(list(events), self._batch_size, self._max_batch_bytes)

    def _backoff(self, attempt: int) -> float:
        return _backoff_ms(self._random, self._backoff_base_ms, attempt)


class IngestClient(_BaseIngestClient):
    """Submits v2 usage events synchronously with batching, retry, and poison isolation."""

    def __init__(
        self,
        server_url: str,
        token: str,
        *,
        batch_size: int = _DEFAULT_BATCH_SIZE,
        max_batch_bytes: int = _DEFAULT_MAX_BATCH_BYTES,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        backoff_base_ms: int = _DEFAULT_BACKOFF_BASE_MS,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        random_fn: Callable[[], float] = random,
    ) -> None:
        """Create the client.

        Args:
            server_url: Base URL of the tokemetry server.
            token: Bearer token with the ``ingest:events`` scope.
            batch_size: Max events per batch.
            max_batch_bytes: Max uncompressed bytes per batch.
            max_retries: Max retry attempts for a 429/5xx batch.
            backoff_base_ms: Base backoff; attempt n waits up to base * 2**n.
            client: Injected httpx client (for tests); created if omitted.
            sleep: Injected sleep (for tests).
            random_fn: Injected [0, 1) source for jitter (for tests).
        """
        super().__init__(
            server_url,
            token,
            batch_size=batch_size,
            max_batch_bytes=max_batch_bytes,
            max_retries=max_retries,
            backoff_base_ms=backoff_base_ms,
            random_fn=random_fn,
        )
        self._client = client if client is not None else httpx.Client(timeout=30.0)
        self._owns_client = client is None
        self._sleep = sleep

    def __enter__(self) -> IngestClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the HTTP client if this instance created it."""
        if self._owns_client:
            self._client.close()

    def ingest(self, events: Sequence[UsageEventV2]) -> IngestResult:
        """Submit events, batching, retrying, and isolating poison events."""
        result = IngestResult()
        for batch in self._split(events):
            self._submit(batch, result)
        return result

    def _submit(self, batch: list[UsageEventV2], result: IngestResult) -> None:
        response = self._post(batch)
        result.batches += 1
        disposition = _classify(response.status_code, len(batch))
        if disposition is _Disposition.ACCEPT:
            result.accepted += _accepted_count(response, len(batch))
        elif disposition is _Disposition.AUTH_FAILED:
            raise IngestAuthError("ingest paused: authentication rejected (401)")
        elif disposition is _Disposition.POISON:
            result.rejected += 1
            result.poison_events.append(batch[0])
        elif disposition is _Disposition.BISECT:
            mid = len(batch) // 2
            self._submit(batch[:mid], result)
            self._submit(batch[mid:], result)
        else:  # RETRY_EXHAUSTED
            raise IngestRetryError(response.status_code)

    def _post(self, batch: list[UsageEventV2]) -> httpx.Response:
        payload = _payload(batch)
        last = 0
        for attempt in range(self._max_retries + 1):
            response = self._client.post(self._url, json=payload, headers=self._headers)
            if response.status_code != 429 and response.status_code < 500:
                return response
            last = response.status_code
            if attempt < self._max_retries:
                self._sleep(self._backoff(attempt) / 1000.0)
        raise IngestRetryError(last)


class AsyncIngestClient(_BaseIngestClient):
    """Async twin of :class:`IngestClient` (httpx.AsyncClient), same semantics."""

    def __init__(
        self,
        server_url: str,
        token: str,
        *,
        batch_size: int = _DEFAULT_BATCH_SIZE,
        max_batch_bytes: int = _DEFAULT_MAX_BATCH_BYTES,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        backoff_base_ms: int = _DEFAULT_BACKOFF_BASE_MS,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        random_fn: Callable[[], float] = random,
    ) -> None:
        """Create the async client. See :class:`IngestClient` for arg semantics.

        ``sleep`` is an async callable taking seconds (defaults to
        ``asyncio.sleep``; trio users should inject ``trio.sleep``);
        ``client`` is an injected ``httpx.AsyncClient`` for tests.
        """
        super().__init__(
            server_url,
            token,
            batch_size=batch_size,
            max_batch_bytes=max_batch_bytes,
            max_retries=max_retries,
            backoff_base_ms=backoff_base_ms,
            random_fn=random_fn,
        )
        self._client = client if client is not None else httpx.AsyncClient(timeout=30.0)
        self._owns_client = client is None
        self._sleep = sleep

    async def __aenter__(self) -> AsyncIngestClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the HTTP client if this instance created it."""
        if self._owns_client:
            await self._client.aclose()

    async def ingest(self, events: Sequence[UsageEventV2]) -> IngestResult:
        """Submit events, batching, retrying, and isolating poison events."""
        result = IngestResult()
        for batch in self._split(events):
            await self._submit(batch, result)
        return result

    async def _submit(self, batch: list[UsageEventV2], result: IngestResult) -> None:
        response = await self._post(batch)
        result.batches += 1
        disposition = _classify(response.status_code, len(batch))
        if disposition is _Disposition.ACCEPT:
            result.accepted += _accepted_count(response, len(batch))
        elif disposition is _Disposition.AUTH_FAILED:
            raise IngestAuthError("ingest paused: authentication rejected (401)")
        elif disposition is _Disposition.POISON:
            result.rejected += 1
            result.poison_events.append(batch[0])
        elif disposition is _Disposition.BISECT:
            mid = len(batch) // 2
            await self._submit(batch[:mid], result)
            await self._submit(batch[mid:], result)
        else:  # RETRY_EXHAUSTED
            raise IngestRetryError(response.status_code)

    async def _post(self, batch: list[UsageEventV2]) -> httpx.Response:
        payload = _payload(batch)
        last = 0
        for attempt in range(self._max_retries + 1):
            response = await self._client.post(
                self._url, json=payload, headers=self._headers
            )
            if response.status_code != 429 and response.status_code < 500:
                return response
            last = response.status_code
            if attempt < self._max_retries:
                await self._sleep(self._backoff(attempt) / 1000.0)
        raise IngestRetryError(last)
