"""The telemetry plug-in's ``register``: what it attaches to a service."""

import asyncio
import logging
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI

from app import platform_events, plugins
from app.plugins.telemetry import UsageCollection, listeners, plugin
from app.plugins.telemetry.middleware import MetricsMiddleware
from app.plugins.telemetry.pusher import MetricsPusher


@pytest.fixture
def host():
    loaded = plugins.load_plugins("indexing_service", ["app.plugins.telemetry"])
    yield loaded
    loaded.close()


def test_the_plugin_satisfies_the_protocol_and_loads_from_the_manifest_path(host) -> None:
    assert isinstance(plugin, plugins.Plugin)
    assert host.loaded == ["telemetry"] and host.failures == []


def test_install_registers_the_metrics_middleware_for_the_service(host) -> None:
    app = FastAPI()
    host.install(app)
    [middleware] = [m for m in app.user_middleware if m.cls is MetricsMiddleware]
    assert middleware.kwargs == {"service_name": "indexing_service"}


def test_it_provides_the_usage_collection_capability_until_closed() -> None:
    before = plugins.get_capability(plugins.USAGE_COLLECTION)
    loaded = plugins.load_plugins("query_service", ["app.plugins.telemetry"])
    capability = plugins.get_capability(plugins.USAGE_COLLECTION)
    assert isinstance(capability, UsageCollection) and capability.enabled is True
    assert "installed and on" in capability.describe()
    loaded.close()
    assert plugins.get_capability(plugins.USAGE_COLLECTION) in (None, before)


def test_every_handler_listens_exactly_once_however_many_services_load_the_plugin(host) -> None:
    second = plugins.load_plugins("query_service", ["app.plugins.telemetry"])
    try:
        for name, handler in listeners.handlers().items():
            assert sum(1 for h in platform_events._listeners.get(name, ()) if h is handler) == 1, name
    finally:
        second.close()


async def test_start_starts_a_pusher_bound_to_the_service_and_stop_stops_it(host, monkeypatch) -> None:
    started, stopped = [], []

    async def start(self) -> None:
        started.append((self._service_name, self._config_service))

    async def stop(self) -> None:
        stopped.append(self._service_name)

    monkeypatch.setattr(MetricsPusher, "start", start)
    monkeypatch.setattr(MetricsPusher, "stop", stop)
    config_service = MagicMock()
    await host.start(config_service=config_service, logger=logging.getLogger("test"))
    assert started == [("indexing_service", config_service)]
    await host.stop()
    await host.stop()
    assert stopped == ["indexing_service"]


async def test_a_pusher_that_cannot_start_is_reported_and_does_not_stop_the_service(host, monkeypatch) -> None:
    async def start(self) -> None:
        raise RuntimeError("no config store")

    monkeypatch.setattr(MetricsPusher, "start", start)
    await host.start(config_service=MagicMock(), logger=logging.getLogger("test"))
    assert [(f.plugin, f.stage) for f in host.failures] == [("telemetry", "start")]


def test_switched_off_it_adds_no_middleware_and_no_listener(monkeypatch) -> None:
    monkeypatch.setenv("TELEMETRY_COLLECTION_FLAG", "false")
    calls = []
    monkeypatch.setattr(listeners, "subscribe", lambda on: calls.append(on))
    loaded = plugins.load_plugins("query_service", ["app.plugins.telemetry"])
    try:
        app = FastAPI()
        loaded.install(app)
        assert not [m for m in app.user_middleware if m.cls is MetricsMiddleware]
        assert calls == []
        capability = plugins.get_capability(plugins.USAGE_COLLECTION)
        assert capability.enabled is False and "nothing is collected" in capability.describe()
    finally:
        loaded.close()


def test_the_ops_exporter_runs_from_governor_start_to_governor_stop(monkeypatch) -> None:
    from app.plugins.telemetry import ops_exporter

    events = []

    class Exporter:
        def start(self) -> None:
            events.append("start")

        def stop(self) -> None:
            events.append("stop")

    seen = []
    monkeypatch.setattr(ops_exporter, "exporter_from_env", lambda governor, service: seen.append((governor, service)) or Exporter())
    loaded = plugins.load_plugins("indexing_service", ["app.plugins.telemetry"])
    try:
        app = FastAPI()
        loaded.install(app)
        platform_events.emit("resource_governor.started", service="indexing")
        assert events == []  # no governor on the app yet: nothing to export
        app.state.governor = governor = object()
        platform_events.emit("resource_governor.started", service="indexing")
        platform_events.emit("resource_governor.started", service="indexing")
        assert seen == [(governor, "indexing")] and events == ["start"]
        asyncio.run(loaded.stop())  # the pusher stops first; the exporter serves until the governor has stopped
        assert events == ["start"]
        platform_events.emit("resource_governor.stopped", service="indexing")
        platform_events.emit("resource_governor.stopped", service="indexing")
        assert events == ["start", "stop"]
    finally:
        loaded.close()


def test_the_ops_exporter_stays_off_unless_its_own_switch_is_on(monkeypatch) -> None:
    monkeypatch.delenv("PIPESHUB_METRICS_ENABLED", raising=False)
    loaded = plugins.load_plugins("indexing_service", ["app.plugins.telemetry"])
    try:
        app = FastAPI()
        loaded.install(app)
        app.state.governor = object()
        platform_events.emit("resource_governor.started", service="indexing")
        platform_events.emit("resource_governor.stopped", service="indexing")
    finally:
        loaded.close()
