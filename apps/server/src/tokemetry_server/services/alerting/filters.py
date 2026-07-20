"""Alert-rule dimension filters (Task 68.1, FR-ALERT-002).

An alert rule may scope its evaluation to a subset of usage by provider, model,
source, project, and/or environment. Filters live under ``AlertRule.config``
(``config["filters"]``) as lists -- an event matches a dimension when its value
is in the rule's list, and an absent or empty list means "any" (so a rule with
no filters behaves exactly as before, FR-ALERT-001).

:func:`filters_from_config` parses a rule's config; :func:`apply_ledger_filters`
scopes a ``usage_events_v2`` query with IN clauses; :meth:`AlertFilters.scoped_dimensions`
names the dimensions that were scoped so the alert event can record its scope
without recording any raw value (content-free, FR-ALERT-010).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, select

from tokemetry_server.db import models

#: The dimensions a rule may filter on, in stable order.
ALERT_FILTER_DIMENSIONS: tuple[str, ...] = (
    "provider",
    "model",
    "source",
    "project",
    "environment",
)


@dataclass(frozen=True)
class AlertFilters:
    """A rule's dimension filters; an empty tuple means the dimension is unscoped."""

    provider: tuple[str, ...] = ()
    model: tuple[str, ...] = ()
    source: tuple[str, ...] = ()
    project: tuple[str, ...] = ()
    environment: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        """Whether no dimension is scoped (the rule matches all usage)."""
        return not any(
            (self.provider, self.model, self.source, self.project, self.environment)
        )

    def scoped_dimensions(self) -> list[str]:
        """The names of the scoped dimensions (never their values -- content-free)."""
        pairs = (
            ("provider", self.provider),
            ("model", self.model),
            ("source", self.source),
            ("project", self.project),
            ("environment", self.environment),
        )
        return [name for name, values in pairs if values]


def filters_from_config(config: dict[str, Any] | None) -> AlertFilters:
    """Parse a rule's ``config['filters']`` into an :class:`AlertFilters`.

    Missing or empty filters yield an all-empty (unscoped) instance, so existing
    rules with no ``filters`` key are unchanged.
    """
    raw = (config or {}).get("filters") or {}

    def _values(key: str) -> tuple[str, ...]:
        value = raw.get(key) or []
        return tuple(str(item) for item in value)

    return AlertFilters(
        provider=_values("provider"),
        model=_values("model"),
        source=_values("source"),
        project=_values("project"),
        environment=_values("environment"),
    )


def apply_ledger_filters(statement: Select[Any], filters: AlertFilters) -> Select[Any]:
    """Scope a ``usage_events_v2`` query to a rule's filters (IN semantics).

    ``source`` matches by source name via the ``sources`` registry; the other
    dimensions match their columns directly. An unscoped dimension adds no clause.
    """
    event = models.UsageEventV2
    if filters.provider:
        statement = statement.where(event.provider.in_(filters.provider))
    if filters.model:
        statement = statement.where(event.native_model.in_(filters.model))
    if filters.project:
        statement = statement.where(event.project.in_(filters.project))
    if filters.environment:
        statement = statement.where(event.environment.in_(filters.environment))
    if filters.source:
        source_ids = select(models.Source.id).where(models.Source.name.in_(filters.source))
        statement = statement.where(event.source_id.in_(source_ids))
    return statement
