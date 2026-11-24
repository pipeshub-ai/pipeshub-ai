"""The plug-in loader: what a service entry module and a plug-in may rely on."""

import asyncio
import logging
import sys
import types
from typing import Never

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import platform_events, plugins


def _module(monkeypatch, name, plugin=None, **attrs):
    module = types.ModuleType(name)
    if plugin is not None:
        module.plugin = plugin
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)
    return module


class _Recording:
    def __init__(self, name, log) -> None:
        self.name = name
        self._log = log

    def register(self, ctx) -> None:
        self._log.append(f"register:{self.name}:{ctx.service_name}")

        async def start(runtime) -> None:
            self._log.append(f"start:{self.name}:{runtime.service_name}")

        async def stop() -> None:
            self._log.append(f"stop:{self.name}")

        ctx.on_start(start)
        ctx.on_stop(stop)


def test_load_plugins_imports_the_manifest_modules_and_registers_each(monkeypatch) -> None:
    log = []
    _module(monkeypatch, "fake_plugin_a", _Recording("a", log))
    _module(monkeypatch, "fake_plugin_b", _Recording("b", log))
    host = plugins.load_plugins("svc", ["fake_plugin_a", "fake_plugin_b"])
    assert host.loaded == ["a", "b"]
    assert host.failures == []
    assert log == ["register:a:svc", "register:b:svc"]


def test_load_plugins_reads_the_manifest_module_by_default(monkeypatch) -> None:
    log = []
    _module(monkeypatch, "fake_plugin_a", _Recording("a", log))
    _module(monkeypatch, "app.plugins_manifest", PLUGINS=["fake_plugin_a"])
    assert plugins.load_plugins("svc").loaded == ["a"]


def test_an_empty_manifest_loads_nothing_and_the_service_still_starts_and_stops() -> None:
    host = plugins.load_plugins("svc", [])
    app = FastAPI()
    host.install(app)
    records = []
    logger = logging.getLogger("test.plugins.empty")
    logger.addHandler(type("H", (logging.Handler,), {"emit": lambda self, record: records.append(record.getMessage())})())
    logger.setLevel(logging.INFO)

    async def run() -> None:
        await host.start(config_service=object(), logger=logger)
        await host.stop()

    saved = plugins.get_capability(plugins.USAGE_COLLECTION)
    plugins._capabilities.pop(plugins.USAGE_COLLECTION, None)
    try:
        asyncio.run(run())
    finally:
        if saved is not None:
            plugins._capabilities[plugins.USAGE_COLLECTION] = saved
    assert host.loaded == [] and host.failures == []
    assert any("not installed; nothing is collected" in line for line in records)


def test_a_plugin_that_raises_in_register_is_reported_and_the_rest_load(monkeypatch, caplog) -> None:
    log = []

    class Broken:
        name = "broken"

        def register(self, ctx) -> Never:
            raise RuntimeError("cannot register")

    _module(monkeypatch, "fake_plugin_broken", Broken())
    _module(monkeypatch, "fake_plugin_a", _Recording("a", log))
    with caplog.at_level(logging.WARNING, logger="app.plugins"):
        host = plugins.load_plugins("svc", ["fake_plugin_broken", "fake_plugin_missing_module", "fake_plugin_a"])
    assert host.loaded == ["a"]
    assert [(f.plugin, f.stage) for f in host.failures] == [("broken", "register"), ("fake_plugin_missing_module", "import")]
    assert "cannot register" in host.failures[0].error
    assert "broken" in caplog.text and "fake_plugin_missing_module" in caplog.text


def test_a_module_without_a_plugin_object_is_reported(monkeypatch) -> None:
    _module(monkeypatch, "fake_plugin_empty")
    host = plugins.load_plugins("svc", ["fake_plugin_empty"])
    assert [(f.plugin, f.stage) for f in host.failures] == [("fake_plugin_empty", "import")]


async def test_start_hooks_run_in_order_and_stop_hooks_in_reverse(monkeypatch) -> None:
    log = []
    _module(monkeypatch, "fake_plugin_a", _Recording("a", log))
    _module(monkeypatch, "fake_plugin_b", _Recording("b", log))
    host = plugins.load_plugins("svc", ["fake_plugin_a", "fake_plugin_b"])
    log.clear()
    await host.start(config_service=object(), logger=logging.getLogger("test"))
    await host.stop()
    assert log == ["start:a:svc", "start:b:svc", "stop:b", "stop:a"]


async def test_a_failing_start_or_stop_hook_is_reported_and_the_others_run(monkeypatch) -> None:
    log = []

    class Flaky:
        name = "flaky"

        def register(self, ctx) -> None:
            async def start(runtime) -> Never:
                raise RuntimeError("no start")

            async def stop() -> Never:
                raise RuntimeError("no stop")

            ctx.on_start(start)
            ctx.on_stop(stop)

    _module(monkeypatch, "fake_plugin_flaky", Flaky())
    _module(monkeypatch, "fake_plugin_a", _Recording("a", log))
    host = plugins.load_plugins("svc", ["fake_plugin_flaky", "fake_plugin_a"])
    await host.start(config_service=object(), logger=logging.getLogger("test"))
    await host.stop()
    assert log[-2:] == ["start:a:svc", "stop:a"]
    assert [(f.plugin, f.stage) for f in host.failures] == [("flaky", "start"), ("flaky", "stop")]


async def test_a_cancelled_stop_hook_propagates_and_still_closes_the_host(monkeypatch) -> None:
    seen = []

    class Slow:
        name = "slow"

        def register(self, ctx) -> None:
            async def stop() -> Never:
                raise asyncio.CancelledError

            ctx.on("test.plugins.cancel", lambda name, payload: seen.append(1))
            ctx.on_stop(stop)

    _module(monkeypatch, "fake_plugin_slow", Slow())
    host = plugins.load_plugins("svc", ["fake_plugin_slow"])
    with pytest.raises(asyncio.CancelledError):
        await host.stop()
    platform_events.emit("test.plugins.cancel")
    assert seen == []


async def test_event_subscriptions_made_through_the_context_end_when_the_host_stops(monkeypatch) -> None:
    seen = []

    class Listener:
        name = "listener"

        def register(self, ctx) -> None:
            ctx.on("test.plugins.event", lambda name, payload: seen.append(payload["n"]))

    _module(monkeypatch, "fake_plugin_listener", Listener())
    host = plugins.load_plugins("svc", ["fake_plugin_listener"])
    platform_events.emit("test.plugins.event", n=1)
    await host.stop()
    platform_events.emit("test.plugins.event", n=2)
    assert seen == [1]


def test_capabilities_are_none_when_absent_and_withdrawn_when_the_host_closes(monkeypatch) -> None:
    offered = object()

    class Provider:
        name = "provider"

        def register(self, ctx) -> None:
            ctx.provide("test_capability", offered)

    assert plugins.get_capability("test_capability") is None
    _module(monkeypatch, "fake_plugin_provider", Provider())
    host = plugins.load_plugins("svc", ["fake_plugin_provider"])
    assert plugins.get_capability("test_capability") is offered
    host.close()
    assert plugins.get_capability("test_capability") is None


def test_install_adds_asgi_middleware_and_runs_app_hooks(monkeypatch) -> None:
    calls = []

    class Counting:
        def __init__(self, app, label) -> None:
            self.app = app
            self.label = label

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                calls.append(self.label)
            await self.app(scope, receive, send)

    class Web:
        name = "web"

        def register(self, ctx) -> None:
            ctx.add_asgi_middleware(Counting, label=ctx.service_name)
            ctx.on_app(lambda app: setattr(app.state, "seen_by_plugin", True))

    _module(monkeypatch, "fake_plugin_web", Web())
    host = plugins.load_plugins("svc", ["fake_plugin_web"])
    app = FastAPI()

    @app.get("/ping")
    def ping():
        return {"ok": True}

    host.install(app)
    assert TestClient(app).get("/ping").status_code == 200
    assert calls == ["svc"] and app.state.seen_by_plugin is True


def test_a_failing_app_hook_is_reported_and_does_not_stop_the_install(monkeypatch) -> None:
    class Web:
        name = "web"

        def register(self, ctx) -> None:
            ctx.on_app(lambda app: 1 / 0)
            ctx.on_app(lambda app: setattr(app.state, "second", True))

    _module(monkeypatch, "fake_plugin_web", Web())
    host = plugins.load_plugins("svc", ["fake_plugin_web"])
    app = FastAPI()
    host.install(app)
    assert app.state.second is True
    assert [(f.plugin, f.stage) for f in host.failures] == [("web", "install")]
