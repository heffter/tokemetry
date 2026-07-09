"""Pricing: LiteLLM price import, date-versioned resolution, strategies.

Price data flows in three stages: raw LiteLLM JSON is transformed into
:class:`~tokemetry_core.models.PriceRow` rows (``litellm.py``), rows are
resolved by model id and date with override support (``table.py``), and a
provider :class:`~tokemetry_core.interfaces.PricingStrategy` turns a row
plus a usage event into USD (``anthropic.py``).
"""
