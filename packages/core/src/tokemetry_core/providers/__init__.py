"""Provider adapter implementations.

Each submodule implements the core interfaces for one provider and exposes
a ``register(registry)`` function. The ``fake`` provider exists only for
tests: it proves the interfaces are sufficient without any real-provider
assumptions.
"""
