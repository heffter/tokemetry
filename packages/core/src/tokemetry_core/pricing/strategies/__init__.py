"""Provider-neutral v2 pricing strategy plugins (TOK-5, PP-011).

Each module implements :class:`~tokemetry_core.interfaces.ProviderPricingStrategyV2`
for one provider and declares the token unit types that provider bills plus its
reasoning rule. ``defaults`` wires the built-in plugins (anthropic, openai, zai,
and the generic fallback) onto a registry. Import the concrete strategy from its
module; this package intentionally re-exports nothing (matching the ``pricing``
and ``providers`` packages).
"""
