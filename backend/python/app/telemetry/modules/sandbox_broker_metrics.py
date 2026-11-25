"""The sandbox broker's own metrics (C9.04, C9.07): sandbox-seconds by mode and phase, warm claims by outcome, and
the warm pool's depth, on the process's metrics backend, served at the broker's ``GET /metrics``.

Labels are bounded: a runtime mode the harness ships (else ``other``), ``active`` or ``kept``, a claim outcome. Who to
bill for a sandbox's seconds is on the broker's ``sandbox.metered`` log line, never a metric label.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.telemetry.backend import METRICS_BACKEND
from app.telemetry.modules import security_metrics
from harness_core.sandbox.metering import metric_mode

if TYPE_CHECKING:
    from harness_core.sandbox.metering import MeteredInterval, WarmClaim

# The security signals go to this process's registry (SEC-D-13).
security_metrics.install()

SANDBOX_SECONDS = METRICS_BACKEND.counter(
    "pipeshub_harness_sandbox_seconds_total",
    "Seconds sandboxes were bound to runs (active) or kept for a run's next turn (kept), from bind to release",
    ["mode", "phase"],
)
WARM_CLAIMS = METRICS_BACKEND.counter(
    "pipeshub_harness_sandbox_warm_claims_total",
    "Acquires of a shape the warm pool serves: taken warm, gone cold, or sent cold for the org's share",
    ["outcome"],
)
WARM_POOL_DEPTH = METRICS_BACKEND.gauge(
    "pipeshub_harness_sandbox_warm_pool_depth",
    "Unclaimed warm sandboxes the broker holds ready",
    [],
)


class BackendBrokerMetrics:
    """``harness_core.sandbox.metering.BrokerMetrics`` on ``METRICS_BACKEND`` (a no-op backend when telemetry is off)."""

    def metered(self, interval: MeteredInterval) -> None:
        SANDBOX_SECONDS.inc(metric_mode(interval.mode), interval.phase, value=max(interval.seconds, 0.0))

    def warm_claim(self, outcome: WarmClaim) -> None:
        WARM_CLAIMS.inc(outcome)

    def pool_depth(self, depth: int) -> None:
        WARM_POOL_DEPTH.set(value=float(depth))


__all__ = ["BackendBrokerMetrics"]
