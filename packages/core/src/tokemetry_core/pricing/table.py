"""Date-versioned pricing table with model-id resolution and overrides.

Providers ship model ids in two styles -- dated snapshots
(``claude-opus-4-5-20251101``) and undated aliases (``claude-fable-5``) --
and price databases may use either. Resolution therefore falls back from an
exact match to a match on the date-stripped base name in both directions.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any

from tokemetry_core.models import PriceRow

#: Trailing snapshot-date suffix in model ids, for example ``-20251101``.
_DATE_SUFFIX = re.compile(r"-\d{8}$")

#: PriceRow money fields that a manual override may replace.
_OVERRIDABLE_FIELDS = frozenset(
    {
        "input_per_mtok",
        "output_per_mtok",
        "cache_read_per_mtok",
        "cache_write_short_per_mtok",
        "cache_write_long_per_mtok",
    }
)


class UnknownModelError(LookupError):
    """No price row exists for the requested provider/model/date."""


def base_model_id(model: str) -> str:
    """Strip a trailing ``-YYYYMMDD`` snapshot suffix from a model id."""
    return _DATE_SUFFIX.sub("", model)


class PricingTable:
    """Resolves the effective price row for a provider model on a date.

    Rows are grouped by ``(provider, model)``; resolution picks the row
    with the latest ``effective_date`` not after the requested date. When
    the exact model id has no rows, the date-stripped base id is tried,
    covering both directions of the dated/undated mismatch.
    """

    def __init__(self, rows: list[PriceRow] | None = None) -> None:
        """Create a table, optionally pre-populated with ``rows``."""
        self._rows: dict[tuple[str, str], list[PriceRow]] = {}
        for row in rows or []:
            self.add(row)

    def add(self, row: PriceRow) -> None:
        """Add one row, keeping the per-model list sorted by date."""
        key = (row.provider, row.model)
        bucket = self._rows.setdefault(key, [])
        bucket.append(row)
        bucket.sort(key=lambda item: item.effective_date)

    def models(self, provider: str) -> list[str]:
        """All model ids with at least one row for ``provider``."""
        return sorted(model for prov, model in self._rows if prov == provider)

    def resolve(self, provider: str, model: str, on: date) -> PriceRow:
        """Return the price row effective for ``model`` on date ``on``.

        Args:
            provider: Provider name the model belongs to.
            model: Native model id, dated or undated.
            on: The date the price must be effective on.

        Raises:
            UnknownModelError: If neither the exact id nor its
                date-stripped base id has a row effective on ``on``.
        """
        for candidate in self._candidates(provider, model):
            row = self._latest_not_after(candidate, on)
            if row is not None:
                return row
        raise UnknownModelError(f"{provider}/{model} on {on.isoformat()}")

    def _candidates(self, provider: str, model: str) -> list[tuple[str, str]]:
        """Lookup keys to try, most specific first."""
        keys = [(provider, model)]
        base = base_model_id(model)
        if base != model:
            keys.append((provider, base))
        else:
            # Undated query: match a dated snapshot sharing the base name,
            # preferring the lexicographically newest snapshot.
            dated = [
                (prov, mod)
                for prov, mod in self._rows
                if prov == provider and mod != model and base_model_id(mod) == model
            ]
            keys.extend(sorted(dated, key=lambda item: item[1], reverse=True))
        return keys

    def _latest_not_after(self, key: tuple[str, str], on: date) -> PriceRow | None:
        """Latest row for ``key`` effective on or before ``on``, if any."""
        best: PriceRow | None = None
        for row in self._rows.get(key, []):
            if row.effective_date <= on:
                best = row
        return best


def apply_overrides(
    rows: list[PriceRow],
    overrides: dict[str, dict[str, Any]],
    provider: str = "anthropic",
) -> list[PriceRow]:
    """Apply manual per-model price overrides to ``rows``.

    Overrides come from user configuration (TOML), keyed by model id, with
    values for any subset of the price fields, for example::

        [pricing.overrides."claude-fable-5"]
        input_per_mtok = "7.50"

    Args:
        rows: Rows to transform (not mutated; models are frozen).
        overrides: Model id to field/value mapping.
        provider: Provider the overrides belong to.

    Returns:
        A new list where matching rows have the overridden values.

    Raises:
        ValueError: If an override names a field that is not a price field.
    """
    for fields in overrides.values():
        unknown = set(fields) - _OVERRIDABLE_FIELDS
        if unknown:
            raise ValueError(f"non-price override fields: {sorted(unknown)}")

    result: list[PriceRow] = []
    for row in rows:
        row_fields = overrides.get(row.model) if row.provider == provider else None
        if not row_fields:
            result.append(row)
            continue
        updates = {name: Decimal(str(value)) for name, value in row_fields.items()}
        result.append(row.model_copy(update=updates))
    return result
