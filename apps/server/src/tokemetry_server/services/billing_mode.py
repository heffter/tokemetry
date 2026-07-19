"""Billing-mode vocabulary and resolution (D-007, FR-COST-011).

Every event's cost is either **actual API spend** (``api_billed``) or
**subscription-equivalent value** (``subscription``); the two are never merged
(FR-COST-012). The mode is carried on the reporting source
(:class:`~tokemetry_server.db.models.Source`), with an account-level override
map (machine -> mode, from settings) for usage whose source keeps the default
mode -- notably v1 collector events from a subscription (Max) machine, whose
derived collector source defaults to ``api_billed``.

Resolution precedence (FR-COST-011):

1. An explicitly non-default source mode (an operator PATCHed the source to
   ``subscription``) wins.
2. Otherwise an account-level override keyed by the event's machine.
3. Otherwise the source's own mode, falling back to the default ``api_billed``.

Because ``api_billed`` is also the default, a source still at ``api_billed`` is
treated as "unspecified" so the machine override can refine it; an explicit
``subscription`` source always wins.
"""

from __future__ import annotations

from collections.abc import Mapping

#: Actual out-of-pocket API spend.
API_BILLED = "api_billed"

#: Subscription-equivalent value (no real spend); priced at equivalent API rates.
SUBSCRIPTION = "subscription"

#: The mode assumed when nothing else specifies one.
DEFAULT_BILLING_MODE = API_BILLED

#: Every recognized billing mode.
BILLING_MODES: frozenset[str] = frozenset({API_BILLED, SUBSCRIPTION})


def resolve_billing_mode(
    source_billing_mode: str | None,
    machine: str | None,
    overrides: Mapping[str, str],
) -> str:
    """Resolve the billing mode for one event (see module precedence).

    Args:
        source_billing_mode: The event's source ``billing_mode``, or ``None``
            when the event has no attributed source.
        machine: The event's machine, used to key the account-level override.
        overrides: Account-level machine -> billing_mode overrides.

    Returns:
        ``api_billed`` or ``subscription``.
    """
    if source_billing_mode is not None and source_billing_mode != DEFAULT_BILLING_MODE:
        return source_billing_mode
    if machine is not None:
        override = overrides.get(machine)
        if override is not None:
            return override
    return source_billing_mode or DEFAULT_BILLING_MODE
