"""Wire telemetry into a FastAPI app.

Usage (once per FastAPI app):

    from app.telemetry.setup import setup_telemetry

    pusher = setup_telemetry(app, service_name="query_service")
    # in lifespan startup:  await pusher.bind(config_service, logger).start()
    # in lifespan shutdown: await pusher.pusher.stop()
"""

from fastapi import FastAPI

from app.telemetry.middleware import MetricsMiddleware
from app.telemetry.pusher import MetricsPusher


def setup_telemetry(app: FastAPI, service_name: str) -> "PendingPusher":
    """Register the metrics middleware and return a deferred pusher factory.

    The middleware can be added immediately (no dependencies), but the pusher
    needs the ConfigurationService, which is only available inside the lifespan.
    Call ``.bind(config_service, logger)`` there to get a startable pusher.
    """
    app.add_middleware(MetricsMiddleware, service_name=service_name)
    return PendingPusher(service_name)


class PendingPusher:
    """Tiny holder so callers wire start/stop inside lifespan with one line each."""

    def __init__(self, service_name: str) -> None:
        self._service_name = service_name
        self._pusher: MetricsPusher | None = None

    def bind(self, config_service, logger) -> MetricsPusher:
        self._pusher = MetricsPusher(config_service, self._service_name, logger)
        return self._pusher

    @property
    def pusher(self) -> MetricsPusher | None:
        return self._pusher
