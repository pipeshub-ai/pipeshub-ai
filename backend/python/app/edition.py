"""Edition constants that must stay import-free.

This is the only edition switch that ``app.telemetry`` may import: the metrics
backend is chosen at import time, and ``app.edition_config`` pulls in routers
that import telemetry, so importing it from there would cycle.
"""

# Used when TELEMETRY_COLLECTION_FLAG is unset.
TELEMETRY_DEFAULT_ENABLED = True


def structured_logging_provider() -> object:
    """Logging provider override, or ``None`` for the default text logging.

    A function rather than a constant so that resolving a provider never runs at
    import time; see ``app.utils.structured_logging.get_provider``. Typed
    ``object`` so this module stays import-free; ``get_provider`` checks it.
    """
    return None
