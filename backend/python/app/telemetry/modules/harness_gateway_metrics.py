"""The Model Gateway's and Tool Gateway's request metrics (C20.48) and a run's read cache lookups (C7.14), on the
process's metrics backend.

Labels come from ``harness_core.telemetry.metrics``, which bounds every one of them; no org, run, tool name or path
is a label. The process's ``MetricsPusher`` sends them with the rest of its registry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.telemetry.backend import METRICS_BACKEND
from app.telemetry.modules import security_metrics
from harness_core.gateway.errors import TOKEN_REFUSAL_CODES
from harness_core.telemetry import security_signals
from harness_core.telemetry.metrics import (
    ReasonLabels,
    bounded,
    bounded_call,
    bounded_lookup,
)

if TYPE_CHECKING:
    from harness_core.telemetry.metrics import (
        GatewayRequest,
        ReadCacheObserved,
        ToolCallObserved,
    )

# The security signals go to this process's registry (SEC-D-13).
security_metrics.install()

# Engine model calls stream for minutes; a tool call may wait on a person up to its hold.
_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0)

GATEWAY_REQUESTS = METRICS_BACKEND.counter(
    "pipeshub_harness_gateway_requests_total",
    "Requests the Model Gateway and Tool Gateway answered, by route and outcome",
    ["gateway", "route", "outcome"],
)
GATEWAY_REQUEST_DURATION = METRICS_BACKEND.histogram(
    "pipeshub_harness_gateway_request_duration_seconds",
    "Time until a gateway answered a request's last byte",
    ["gateway", "route"],
    buckets=_BUCKETS,
)
GATEWAY_REFUSALS = METRICS_BACKEND.counter(
    "pipeshub_harness_gateway_refusals_total",
    "Requests a gateway refused, by its refusal reason code",
    ["gateway", "route", "reason"],
)
TOOL_GATEWAY_CALLS = METRICS_BACKEND.counter(
    "pipeshub_harness_tool_gateway_calls_total",
    "Tool calls through the Tool Gateway, by the call's effect class and how it ended",
    ["family", "disposition"],
)
TOOL_GATEWAY_CALL_DURATION = METRICS_BACKEND.histogram(
    "pipeshub_harness_tool_gateway_call_duration_seconds",
    "Time a tool call through the Tool Gateway took, by the call's effect class",
    ["family"],
    buckets=_BUCKETS,
)

READ_CACHE_LOOKUPS = METRICS_BACKEND.counter(
    "pipeshub_harness_tool_read_cache_lookups_total",
    "Lookups in a run's cache of its own connector reads (C7.14), by executor and hit, miss or expired",
    ["executor", "outcome"],
)


def is_gateway_token_refusal(reason: str | None) -> bool:
    """A refusal of the run's gateway token itself (``TokenRefused``, ``BindingMismatch``); the gateways' other
    refusals judge a request its token was good for, and the scraper's credential is counted where it is checked."""
    return reason is not None and (reason in TOKEN_REFUSAL_CODES or reason == "binding_mismatch")


class BackendGatewayMetrics:
    """``GatewayMetrics`` on ``METRICS_BACKEND`` (a no-op backend when telemetry is off)."""

    def __init__(self) -> None:
        self._reasons = ReasonLabels()

    def request(self, observed: GatewayRequest) -> None:
        seen = bounded(observed, self._reasons)
        GATEWAY_REQUESTS.inc(seen.gateway, seen.route, seen.outcome)
        GATEWAY_REQUEST_DURATION.observe(seen.gateway, seen.route, value=seen.seconds)
        if seen.outcome == "refused":
            GATEWAY_REFUSALS.inc(seen.gateway, seen.route, seen.reason or "none")
            if is_gateway_token_refusal(observed.reason):
                security_signals.token_rejected("gateway_token", observed.reason)

    def tool_call(self, observed: ToolCallObserved) -> None:
        seen = bounded_call(observed)
        TOOL_GATEWAY_CALLS.inc(seen.family, seen.disposition)
        TOOL_GATEWAY_CALL_DURATION.observe(seen.family, value=seen.seconds)


class BackendReadCacheMetrics:
    """``ReadCacheMetrics`` on ``METRICS_BACKEND``: the read cache's hits beside the gateways' metrics (WP91-FU-1)."""

    def lookup(self, observed: ReadCacheObserved) -> None:
        seen = bounded_lookup(observed)
        READ_CACHE_LOOKUPS.inc(seen.executor, seen.outcome)


READ_CACHE_METRICS = BackendReadCacheMetrics()

__all__ = ["READ_CACHE_METRICS", "BackendGatewayMetrics", "BackendReadCacheMetrics"]
