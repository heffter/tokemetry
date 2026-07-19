"""Rate-card price sources (D-015, FR-PRICE-021).

Each module produces provider-neutral :class:`~tokemetry_core.pricing.sources.rate_card.RateCardRow`
values for the v2 ``rate_cards`` grain (per single ``unit_type``, per-token
price). ``litellm`` transforms the community LiteLLM price database; ``curated``
holds hand-verified official prices that take precedence by ``priority``. The
server import pipeline diffs these rows against the stored rate cards and applies
them as an audited, reviewable change. Import the concrete builder from its
module; this package re-exports nothing.
"""
