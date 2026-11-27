"""The egress proxy's traffic (C6.12): bytes by destination alias and direction, volume alerts, per-run totals.

Labels are bounded (an alias, a scope, a direction, an alert kind): which org or run moved the bytes is on the run's
journal (``egress.volume_alert``), never a metric label. ``scope`` tells PipesHub's own gateways (``gateway``: the
Model and Tool Gateways, counted here only) from the traffic the cap, the alerts and the org baseline judge
(``outside``, WP-61).
"""

from app.telemetry.backend import METRICS_BACKEND
from app.telemetry.modules import security_metrics
from harness_core.sandbox.egress.volume import scope_of

# The security signals go to this process's registry (SEC-D-13).
security_metrics.install()

EGRESS_BYTES = METRICS_BACKEND.counter(
    "pipeshub_egress_bytes_total",
    "Bytes sandboxes sent (up) and received (down) through the egress proxy, by destination alias and scope",
    ["alias", "scope", "direction"],
)
EGRESS_VOLUME_ALERTS = METRICS_BACKEND.counter(
    "pipeshub_egress_volume_alerts_total",
    "Runs whose outside egress went past a per-run threshold, the org's baseline or the hard cap",
    ["kind", "direction"],
)
EGRESS_RUN_BYTES = METRICS_BACKEND.histogram(
    "pipeshub_egress_run_bytes",
    "Bytes one ended run's sandboxes moved through the egress proxy, by scope",
    ["scope", "direction"],
    buckets=[2.0**n for n in range(10, 37, 2)],
)


class PrometheusVolumeMetrics:
    """``harness_core.sandbox.egress.volume.VolumeMetrics`` on the process's metrics backend."""

    def transferred(self, alias: str, direction: str, count: int) -> None:
        EGRESS_BYTES.inc(alias, scope_of(alias), direction, value=float(count))

    def alerted(self, kind: str, direction: str) -> None:
        EGRESS_VOLUME_ALERTS.inc(kind, direction)

    def run_ended(self, scope: str, direction: str, count: int) -> None:
        EGRESS_RUN_BYTES.observe(scope, direction, value=float(count))
