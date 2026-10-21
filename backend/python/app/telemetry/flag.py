"""Master switch for the telemetry package.

``TELEMETRY_COLLECTION_FLAG`` decides whether any telemetry code runs: no
metrics middleware, no registry, no event buffer, no pusher, no gauge refresh
loops when it is off. Unset or empty falls back to the edition default in
``app.edition``. This is separate from the per-install ``enableMetricCollection``
consent in the KV store, which only matters once this flag is on.
"""

import os

from app.edition import METRICS_CONSENT_DEFAULT, TELEMETRY_DEFAULT_ENABLED

TELEMETRY_COLLECTION_FLAG = "TELEMETRY_COLLECTION_FLAG"


def telemetry_enabled() -> bool:
    raw = os.getenv(TELEMETRY_COLLECTION_FLAG, "").strip()
    if not raw:
        return TELEMETRY_DEFAULT_ENABLED
    return raw.lower() == "true"


_consent_given = METRICS_CONSENT_DEFAULT


def set_collection_consent(given: bool) -> None:
    """The admin's Data Collection switch, as the pusher last read it."""
    global _consent_given
    _consent_given = given


def telemetry_collecting() -> bool:
    """Whether anything may be recorded right now: the deployment allows telemetry
    and an admin has switched Data Collection on, so the admin can always see and
    stop whatever is collected."""
    return telemetry_enabled() and _consent_given
