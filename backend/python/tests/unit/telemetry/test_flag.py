"""Unit tests for the TELEMETRY_COLLECTION_FLAG master switch."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI

from app.edition import TELEMETRY_DEFAULT_ENABLED
from app.telemetry.backend import NoopBackend
from app.telemetry.event_buffer import event_buffer, record_event
from app.telemetry.flag import TELEMETRY_COLLECTION_FLAG, telemetry_enabled
from app.telemetry.middleware import MetricsMiddleware
from app.telemetry.pusher import MetricsPusher
from app.telemetry.setup import setup_telemetry


class TestTelemetryEnabled:
    def test_unset_uses_edition_default(self, monkeypatch) -> None:
        monkeypatch.delenv(TELEMETRY_COLLECTION_FLAG, raising=False)

        assert telemetry_enabled() is TELEMETRY_DEFAULT_ENABLED

    @pytest.mark.parametrize("value", ["", "   "])
    def test_empty_uses_edition_default(self, monkeypatch, value) -> None:
        monkeypatch.setenv(TELEMETRY_COLLECTION_FLAG, value)

        assert telemetry_enabled() is TELEMETRY_DEFAULT_ENABLED

    @pytest.mark.parametrize("value", ["true", "TRUE", " True "])
    def test_true_enables(self, monkeypatch, value) -> None:
        monkeypatch.setenv(TELEMETRY_COLLECTION_FLAG, value)

        assert telemetry_enabled() is True

    @pytest.mark.parametrize("value", ["false", "0", "yes", "1"])
    def test_anything_else_disables(self, monkeypatch, value) -> None:
        monkeypatch.setenv(TELEMETRY_COLLECTION_FLAG, value)

        assert telemetry_enabled() is False


class TestNoopBackend:
    def test_instruments_accept_calls_and_serialize_nothing(self) -> None:
        backend = NoopBackend()
        counter = backend.counter("n_counter", "help", ["k"])
        gauge = backend.gauge("n_gauge", "help", ["k"])
        histogram = backend.histogram("n_hist", "help", ["k"], buckets=(1.0,))

        counter.inc("x")
        counter.inc("x", value=3)
        gauge.set("x", value=1)
        gauge.clear()
        histogram.observe("x", value=0.5)
        backend.reset()

        assert backend.serialize() == ""


class TestDisabledWiring:
    def test_setup_skips_middleware(self, monkeypatch) -> None:
        monkeypatch.setenv(TELEMETRY_COLLECTION_FLAG, "false")
        app = FastAPI()

        pending = setup_telemetry(app, service_name="query_service")

        assert MetricsMiddleware not in [m.cls for m in app.user_middleware]
        assert pending.pusher is None

    def test_setup_registers_middleware_when_enabled(self, monkeypatch) -> None:
        monkeypatch.setenv(TELEMETRY_COLLECTION_FLAG, "true")
        app = FastAPI()

        setup_telemetry(app, service_name="query_service")

        assert MetricsMiddleware in [m.cls for m in app.user_middleware]

    async def test_pusher_start_is_noop(self, monkeypatch) -> None:
        monkeypatch.setenv(TELEMETRY_COLLECTION_FLAG, "false")
        config_service = MagicMock()
        config_service.get_config = AsyncMock()
        pusher = MetricsPusher(config_service, "query_service", MagicMock())

        await pusher.start()
        await pusher.stop()

        assert pusher._task is None
        config_service.get_config.assert_not_awaited()

    def test_record_event_drops_events(self, monkeypatch) -> None:
        monkeypatch.setenv(TELEMETRY_COLLECTION_FLAG, "false")
        event_buffer.drain()

        record_event("search_performed", {"org": "o1"})

        assert event_buffer.size() == 0

    def test_record_event_buffers_when_enabled(self, monkeypatch) -> None:
        monkeypatch.setenv(TELEMETRY_COLLECTION_FLAG, "true")
        event_buffer.drain()

        record_event("search_performed", {"org": "o1"})

        assert [e["event"] for e in event_buffer.drain()] == ["search_performed"]
