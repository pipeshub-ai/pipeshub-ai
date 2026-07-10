"""Unit tests for app.telemetry.setup — FastAPI wiring."""

from unittest.mock import MagicMock

from fastapi import FastAPI

from app.telemetry.middleware import MetricsMiddleware
from app.telemetry.pusher import MetricsPusher
from app.telemetry.setup import PendingPusher, setup_telemetry


class TestSetupTelemetry:
    def test_registers_metrics_middleware(self):
        app = FastAPI()

        setup_telemetry(app, service_name="indexing_service")

        middleware_classes = [m.cls for m in app.user_middleware]
        assert MetricsMiddleware in middleware_classes

    def test_returns_pending_pusher_for_lifespan_binding(self):
        app = FastAPI()

        pending = setup_telemetry(app, service_name="indexing_service")

        assert isinstance(pending, PendingPusher)
        assert pending.pusher is None


class TestPendingPusher:
    def test_bind_creates_pusher_with_service_name(self):
        pending = PendingPusher("query_service")
        config_service = MagicMock()
        logger = MagicMock()

        pusher = pending.bind(config_service, logger)

        assert isinstance(pusher, MetricsPusher)
        assert pending.pusher is pusher
        assert pusher._service_name == "query_service"
