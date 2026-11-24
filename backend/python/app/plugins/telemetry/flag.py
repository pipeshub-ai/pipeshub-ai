"""Master switch for the telemetry package.

``TELEMETRY_COLLECTION_FLAG`` decides whether any telemetry code runs: no
metrics middleware, no registry, no event buffer, no pusher, no gauge refresh
loops when it is off. Unset or empty falls back to the edition default in
``app.edition``. This is separate from the per-install ``enableMetricCollection``
consent in the KV store, which only matters once this flag is on.
"""

import os

from app.edition import TELEMETRY_DEFAULT_ENABLED

TELEMETRY_COLLECTION_FLAG = "TELEMETRY_COLLECTION_FLAG"


def telemetry_enabled() -> bool:
    raw = os.getenv(TELEMETRY_COLLECTION_FLAG, "").strip()
    if not raw:
        return TELEMETRY_DEFAULT_ENABLED
    return raw.lower() == "true"
