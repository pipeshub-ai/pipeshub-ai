"""Self-telemetry: whether this install currently consents to metric collection.

Set to ``1`` every push tick while the shared ``enableMetricCollection`` flag
is on. Nothing is recorded or sent while consent is off, so at the collector an
opt-out shows up as the series going stale rather than an explicit ``0``.
"""

from app.telemetry.backend import METRICS_BACKEND

METRIC_COLLECTION_ENABLED = METRICS_BACKEND.gauge(
    "pipeshub_metric_collection_enabled",
    "Metric collection consent for this install (1 = enabled, 0 = disabled)",
    ["service"],
)


def set_metric_collection_enabled(service: str, enabled: bool) -> None:
    METRIC_COLLECTION_ENABLED.set(service, value=1 if enabled else 0)
