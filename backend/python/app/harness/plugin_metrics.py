"""The agent harness's metric ports, as the loaded plug-ins provide them (``app.plugins.get_capability``).

The harness never imports a plug-in. A build without one gets each port's no-op default, and the internal
services' ``GET /metrics`` is left out (``metrics_text`` is ``None``). Asked for when a service builds its parts,
after its entry module loaded the plug-ins.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from app.plugins import (
    EGRESS_VOLUME_METRICS,
    HARNESS_GATEWAY_METRICS,
    HARNESS_READ_CACHE_METRICS,
    METRICS_TEXT,
    SANDBOX_BROKER_METRICS,
    get_capability,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.sandbox.egress.volume import VolumeMetrics
    from harness_core.sandbox.metering import BrokerMetrics
    from harness_core.telemetry.metrics import GatewayMetrics, ReadCacheMetrics


def _provided(name: str) -> Any | None:  # noqa: ANN401 - whatever the capability's factory builds
    factory = get_capability(name)
    return cast("Callable[[], Any]", factory)() if callable(factory) else None


def gateway_metrics() -> GatewayMetrics:
    from harness_core.telemetry.metrics import NOOP_GATEWAY_METRICS

    return _provided(HARNESS_GATEWAY_METRICS) or NOOP_GATEWAY_METRICS


def read_cache_metrics() -> ReadCacheMetrics:
    from harness_core.telemetry.metrics import NOOP_READ_CACHE_METRICS

    return _provided(HARNESS_READ_CACHE_METRICS) or NOOP_READ_CACHE_METRICS


def broker_metrics() -> BrokerMetrics:
    from harness_core.sandbox.metering import NOOP_BROKER_METRICS

    return _provided(SANDBOX_BROKER_METRICS) or NOOP_BROKER_METRICS


def egress_volume_metrics() -> VolumeMetrics | None:
    """``None`` lets ``EgressVolume`` use its own no-op default."""
    return _provided(EGRESS_VOLUME_METRICS)


def metrics_text() -> Callable[[], str] | None:
    """This process's metrics for a ``GET /metrics`` route; ``None`` when no plug-in keeps any."""
    return _provided(METRICS_TEXT)


__all__ = ["broker_metrics", "egress_volume_metrics", "gateway_metrics", "metrics_text", "read_cache_metrics"]
