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
