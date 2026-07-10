"""Connector current-state metrics, refreshed from the graph DB on a tick
(see connectors_main).
"""

from app.telemetry.backend import METRICS_BACKEND

# Gauge is current state, so clearing + re-setting each refresh (to drop removed
# connectors) is correct.
CONNECTOR_ACTIVE = METRICS_BACKEND.gauge(
    "pipeshub_connector_active",
    "Number of active connector instances by connector type",
    ["connector", "domain"],
)


def set_connector_active(counts: dict) -> None:
    """Replace the connector_active series with the current per-type counts.

    ``counts`` keys may be a plain ``connector`` string (domain reported as
    ``"unknown"``) or a ``(connector, domain)`` tuple when the org email-domain
    is known, so dashboards can exclude internal (PipesHub) orgs.
    """
    CONNECTOR_ACTIVE.clear()
    for key, count in counts.items():
        if isinstance(key, tuple):
            connector, domain = (key[0] or "unknown"), (key[1] or "unknown")
        else:
            connector, domain = (key or "unknown"), "unknown"
        CONNECTOR_ACTIVE.set(connector, domain, value=count)
