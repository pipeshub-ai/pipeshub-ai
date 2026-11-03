"""Edition constants that must stay import-free.

This is the only edition switch that ``app.telemetry`` may import: the metrics
backend is chosen at import time, and ``app.edition_config`` pulls in routers
that import telemetry, so importing it from there would cycle.
"""

# Used when TELEMETRY_COLLECTION_FLAG is unset.
TELEMETRY_DEFAULT_ENABLED = True

# Extra migration module directories, relative to ``backend/`` (e.g. ``"db/extra/usage"``), added to the
# ones under ``backend/db/versions/``. Read by ``backend/db`` by file path: keep this module import-free.
EXTRA_MIGRATION_LOCATIONS: list[str] = []


def structured_logging_provider() -> object:
    """Logging provider override, or ``None`` for the default text logging.

    A function rather than a constant so that resolving a provider never runs at
    import time; see ``app.utils.structured_logging.get_provider``. Typed
    ``object`` so this module stays import-free; ``get_provider`` checks it.
    """
    return None
