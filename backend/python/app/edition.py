"""Edition constants that must stay import-free.

This is the only edition switch that ``app.telemetry`` may import: the metrics
backend is chosen at import time, and ``app.edition_config`` pulls in routers
that import telemetry, so importing it from there would cycle.
"""

# Used when TELEMETRY_COLLECTION_FLAG is unset.
TELEMETRY_DEFAULT_ENABLED = True

# The admin's Data Collection switch before an admin has set it.
METRICS_CONSENT_DEFAULT = True
