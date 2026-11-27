"""The agent harness's metric ports on this plug-in's metrics backend, offered as capabilities.

Each is a factory: the harness modules behind them (and the security signals they install, SEC-D-13) are imported
only by a service that asks for one, never by every service that loads the plug-in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.plugins import (
    EGRESS_VOLUME_METRICS,
    HARNESS_GATEWAY_METRICS,
    HARNESS_READ_CACHE_METRICS,
    METRICS_TEXT,
    SANDBOX_BROKER_METRICS,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.plugins import PluginContext


def _metrics_text() -> Callable[[], str]:
    from app.plugins.telemetry.backend import METRICS_BACKEND

    return METRICS_BACKEND.serialize


def _gateway_metrics() -> object:
    from app.plugins.telemetry.modules.harness_gateway_metrics import (
        BackendGatewayMetrics,
    )

    return BackendGatewayMetrics()


def _read_cache_metrics() -> object:
    from app.plugins.telemetry.modules.harness_gateway_metrics import READ_CACHE_METRICS

    return READ_CACHE_METRICS


def _broker_metrics() -> object:
    from app.plugins.telemetry.modules.sandbox_broker_metrics import (
        BackendBrokerMetrics,
    )

    return BackendBrokerMetrics()


def _egress_volume_metrics() -> object:
    from app.plugins.telemetry.modules.egress_metrics import PrometheusVolumeMetrics

    return PrometheusVolumeMetrics()


def provide(ctx: PluginContext) -> None:
    ctx.provide(METRICS_TEXT, _metrics_text)
    ctx.provide(HARNESS_GATEWAY_METRICS, _gateway_metrics)
    ctx.provide(HARNESS_READ_CACHE_METRICS, _read_cache_metrics)
    ctx.provide(SANDBOX_BROKER_METRICS, _broker_metrics)
    ctx.provide(EGRESS_VOLUME_METRICS, _egress_volume_metrics)
