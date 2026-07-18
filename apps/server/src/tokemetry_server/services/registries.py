"""Provider and model registry services (provider-neutral v2, TOK-2).

Two lookup-data services backing the ``providers`` and ``models`` tables:

- :class:`ProviderRegistryService` seeds the built-in providers, resolves a
  raw provider string to its canonical id (core normalizer plus DB aliases),
  and applies the unknown-provider ingest policy (accept-and-mark-unregistered
  or reject) per FR-PROVIDER-005/008.
- :class:`ModelRegistryService` observes native model ids seen during ingest:
  a known model's ``last_seen`` advances; an unknown model is inserted with
  lifecycle ``unknown`` (FR-MODEL-004/006). The data-quality record for a newly
  observed unknown model is emitted by the recording service (subtask 61.4);
  this service surfaces the ``newly_observed`` signal it will consume.

Registries are lookup data only: no usage row carries a foreign key into them
(FR-MODEL-007), so nothing here rewrites historical events.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.models import ProviderDescriptor
from tokemetry_core.normalization import SEED_PROVIDER_DESCRIPTORS, normalize_provider

from tokemetry_server.db import models

#: Seed descriptors indexed by canonical id, the source of truth for which
#: providers count as "registered" even before startup seeding has run.
_SEED_BY_ID: dict[str, ProviderDescriptor] = {d.id: d for d in SEED_PROVIDER_DESCRIPTORS}


def _as_utc(value: datetime | None) -> datetime | None:
    """Coerce a possibly-naive stored timestamp to UTC for comparison.

    SQLite drops the timezone on ``DateTime(timezone=True)`` columns while
    Postgres preserves it; treating a naive value as UTC makes ``last_seen``
    comparisons behave identically on both engines.
    """
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value

#: Valid values for the unknown-provider ingest policy.
UNKNOWN_PROVIDER_POLICIES = ("accept", "reject")


async def seed_default_providers(session: AsyncSession) -> int:
    """Insert the built-in provider descriptors on an empty grain.

    Idempotent (FR-PROVIDER-008): existing rows are left untouched so UI edits
    survive a restart; only missing seed providers are inserted. Returns the
    number of rows inserted.
    """
    now = datetime.now(UTC)
    inserted = 0
    for descriptor in SEED_PROVIDER_DESCRIPTORS:
        if await session.get(models.Provider, descriptor.id) is not None:
            continue
        session.add(_provider_row(descriptor, registered=True, now=now))
        inserted += 1
    return inserted


def _provider_row(
    descriptor: ProviderDescriptor, *, registered: bool, now: datetime
) -> models.Provider:
    """Build a ``providers`` ORM row from a core descriptor."""
    return models.Provider(
        id=descriptor.id,
        display_name=descriptor.display_name,
        aliases=list(descriptor.aliases),
        pricing_strategy=descriptor.pricing_strategy,
        limit_semantics=descriptor.limit_semantics,
        supported_dimensions=list(descriptor.supported_dimensions),
        registered=registered,
        created_at=now,
        updated_at=now,
    )


@dataclass(frozen=True)
class ProviderResolution:
    """Outcome of resolving a raw provider string for ingest."""

    #: Canonical (normalized) provider id.
    provider: str
    #: Whether the ingest policy accepts events for this provider.
    accepted: bool
    #: Whether the provider is a known/registered one (vs an observed unknown).
    registered: bool


class ProviderRegistryService:
    """Resolve and register providers against the ``providers`` table.

    Loads the provider rows once per instance (the table is tiny) and merges
    their aliases with the core normalizer so both seed aliases and any
    DB-only aliases resolve. The in-memory view is kept in sync as rows are
    inserted, so a single request never re-reads the table.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Create the service bound to ``session`` (caller owns the txn)."""
        self._session = session
        self._loaded = False
        self._alias_to_id: dict[str, str] = {}
        self._registered: set[str] = set()

    async def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        rows = (await self._session.execute(select(models.Provider))).scalars().all()
        for row in rows:
            self._index(row.id, row.aliases or [], row.registered)
        self._loaded = True

    def _index(self, provider_id: str, aliases: list[str], registered: bool) -> None:
        """Add a provider (and its aliases) to the in-memory resolution view."""
        self._alias_to_id[provider_id] = provider_id
        for alias in aliases:
            self._alias_to_id[alias] = provider_id
        if registered:
            self._registered.add(provider_id)

    async def normalize(self, raw: str) -> str:
        """Return the canonical id for ``raw`` (DB aliases then core rules)."""
        await self._ensure_loaded()
        cleaned = raw.strip().lower()
        if cleaned in self._alias_to_id:
            return self._alias_to_id[cleaned]
        return normalize_provider(cleaned)

    async def resolve(self, raw: str, policy: str) -> ProviderResolution:
        """Resolve ``raw`` and register it as needed under ``policy``.

        A registered provider (or a known core seed not yet persisted) is
        accepted and ensured present with ``registered=True``. An otherwise
        unknown provider is accepted and marked ``registered=False`` under the
        ``accept`` policy (FR-PROVIDER-005), or rejected under ``reject``.
        """
        canonical = await self.normalize(raw)
        if canonical in self._registered:
            return ProviderResolution(canonical, accepted=True, registered=True)

        seed = _SEED_BY_ID.get(canonical)
        if seed is not None:
            await self._ensure_row(_provider_row(seed, registered=True, now=self._now()))
            self._index(seed.id, list(seed.aliases), registered=True)
            return ProviderResolution(canonical, accepted=True, registered=True)

        if policy == "reject":
            return ProviderResolution(canonical, accepted=False, registered=False)

        await self._ensure_row(self._unknown_provider(canonical))
        self._index(canonical, [], registered=False)
        return ProviderResolution(canonical, accepted=True, registered=False)

    async def _ensure_row(self, row: models.Provider) -> None:
        """Insert a provider row unless it already exists (get-or-create)."""
        if await self._session.get(models.Provider, row.id) is None:
            self._session.add(row)

    def _unknown_provider(self, provider_id: str) -> models.Provider:
        """Build an observed-but-unknown provider row (registered=False)."""
        now = self._now()
        return models.Provider(
            id=provider_id,
            display_name=provider_id,
            aliases=[],
            pricing_strategy="",
            limit_semantics="none",
            supported_dimensions=[],
            registered=False,
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True)
class ModelObservation:
    """Outcome of observing a native model id during ingest."""

    provider: str
    native_model_id: str
    #: True when this observation inserted a new (unknown-lifecycle) model,
    #: which the data-quality recorder (subtask 61.4) turns into a record.
    newly_observed: bool
    #: The model's lifecycle after the observation.
    lifecycle: str


class ModelRegistryService:
    """Observe native model ids against the ``models`` table."""

    def __init__(self, session: AsyncSession) -> None:
        """Create the service bound to ``session`` (caller owns the txn)."""
        self._session = session

    async def observe(
        self, provider: str, native_model_id: str, ts: datetime | None
    ) -> ModelObservation:
        """Record that ``native_model_id`` was seen for ``provider``.

        A known model advances ``last_seen`` when ``ts`` is newer; an unknown
        model is inserted with lifecycle ``unknown`` (FR-MODEL-004). Never
        clobbers an existing lifecycle or capabilities (FR-MODEL-007).
        """
        row = await self._session.get(models.Model, (provider, native_model_id))
        if row is None:
            self._session.add(
                models.Model(
                    provider=provider,
                    native_model_id=native_model_id,
                    lifecycle="unknown",
                    capabilities={},
                    first_seen=ts,
                    last_seen=ts,
                )
            )
            return ModelObservation(provider, native_model_id, True, "unknown")

        incoming = _as_utc(ts)
        stored = _as_utc(row.last_seen)
        if incoming is not None and (stored is None or incoming > stored):
            row.last_seen = ts
        return ModelObservation(provider, native_model_id, False, row.lifecycle)
