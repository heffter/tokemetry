"""tokemetry core: shared domain models, provider abstractions, and pricing.

This package holds everything that must be identical on both sides of the
wire: normalized usage event models, the provider abstraction interfaces
(``UsageSource``, ``LimitsSource``, ``PricingStrategy``), the provider
registry, and pricing computation. Provider-specific knowledge lives behind
these interfaces so new providers can be added without touching consumers.
"""

__version__ = "0.1.0"
