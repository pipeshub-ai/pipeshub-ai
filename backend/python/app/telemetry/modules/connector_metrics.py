"""Connector current-state metrics, refreshed from the graph DB on a tick
(see connectors_main).
"""

from app.telemetry.backend import METRICS_BACKEND

# Gauge is current state, so clearing + re-setting each refresh (to drop removed
# connectors) is correct.
CONNECTOR_ACTIVE = METRICS_BACKEND.gauge(
    "pipeshub_connector_active",
    "Number of active connector instances by connector type",
    ["connector"],
)


def set_connector_active(counts: dict[str, int]) -> None:
    """Replace the connector_active series with the current per-type counts."""
    CONNECTOR_ACTIVE.clear()
    for connector, count in counts.items():
        CONNECTOR_ACTIVE.set(connector or "unknown", value=count)
