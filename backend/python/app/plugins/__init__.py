"""Plug-ins: optional parts of a build that listen to what the application does.

A plug-in is a module named in ``app.plugins_manifest.PLUGINS`` that exposes a
``plugin`` object with a ``name`` and a ``register(ctx)``. The application never
imports a plug-in by name; a build that leaves one out deletes its package and
its manifest line, and every service still starts.

A service entry module wires them in three places::

    plugin_host = plugins.load_plugins("query_service")
    plugin_host.install(app)                       # where middleware is added
    await plugin_host.start(config_service=..., logger=...)   # lifespan start
    await plugin_host.stop()                                   # lifespan end

What a plug-in may do with its ``PluginContext``:

* ``on(name, handler)``: listen to a platform event (``app.platform_events``).
  The subscription lasts as long as the process: what a service does while it
  shuts down, after the stop hooks, is still heard. ``PluginHost.close()`` ends it.
* ``add_asgi_middleware(cls, **options)``: instrument every request of the
  service's FastAPI app. ``on_app(fn)`` receives the app itself.
* ``on_start(coro_fn)``: ``await coro_fn(runtime)`` at service start, in
  registration order. ``on_stop(coro_fn)``: ``await coro_fn()`` at service
  stop, in reverse order.
* ``provide(capability_name, obj)``: offer something the application may ask
  for with ``get_capability(name)``, which answers ``None`` when no plug-in
  provides it.

A plug-in that cannot be imported, or whose ``register`` or start hook raises,
is logged and reported in ``PluginHost.failures``; it never stops a service.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from app import platform_events

if TYPE_CHECKING:
    from fastapi import FastAPI

_log = logging.getLogger(__name__)

# What the application reads to say whether anything about its use is collected.
USAGE_COLLECTION = "usage_collection"

# What the agent harness's services ask for (``app.harness.plugin_metrics``), each a zero-argument factory. None
# provided: the harness ports keep their no-op defaults and the internal services serve no ``/metrics`` route.
METRICS_TEXT = "metrics_text"  # -> Callable[[], str]: this process's metrics in the Prometheus text format
HARNESS_GATEWAY_METRICS = "harness_gateway_metrics"  # -> harness_core.telemetry.metrics.GatewayMetrics
HARNESS_READ_CACHE_METRICS = "harness_read_cache_metrics"  # -> harness_core.telemetry.metrics.ReadCacheMetrics
SANDBOX_BROKER_METRICS = "sandbox_broker_metrics"  # -> harness_core.sandbox.metering.BrokerMetrics
EGRESS_VOLUME_METRICS = "egress_volume_metrics"  # -> harness_core.sandbox.egress.volume.VolumeMetrics


@dataclass(frozen=True)
class ServiceRuntime:
    """What a start hook receives: the service it runs in and its shared services."""

    service_name: str
    config_service: Any
    logger: Any


StartHook = Callable[[ServiceRuntime], Awaitable[None]]
StopHook = Callable[[], Awaitable[None]]


@runtime_checkable
class Plugin(Protocol):
    name: str

    def register(self, ctx: PluginContext) -> None: ...


@dataclass(frozen=True)
class PluginFailure:
    plugin: str
    stage: str  # "import", "register", "install", "start" or "stop"
    error: str


_capabilities: dict[str, object] = {}


def get_capability(name: str) -> object | None:
    """What a loaded plug-in provides under ``name``, or ``None`` when none does."""
    return _capabilities.get(name)


class PluginContext:
    """Handed to ``Plugin.register``: everything a plug-in may attach to a service."""

    def __init__(self, host: PluginHost, plugin_name: str) -> None:
        self._host = host
        self._plugin = plugin_name
        self.service_name = host.service_name

    def on(self, name: str, handler: platform_events.Handler) -> Callable[[], None]:
        unsubscribe = platform_events.on(name, handler)
        self._host._unsubscribes.append(unsubscribe)
        return unsubscribe

    def add_asgi_middleware(self, middleware: type, **options: Any) -> None:  # noqa: ANN401 - the middleware's own options
        self._host._app_hooks.append((self._plugin, lambda app: app.add_middleware(middleware, **options)))

    def on_app(self, hook: Callable[[FastAPI], None]) -> None:
        self._host._app_hooks.append((self._plugin, hook))

    def on_start(self, hook: StartHook) -> None:
        self._host._start_hooks.append((self._plugin, hook))

    def on_stop(self, hook: StopHook) -> None:
        self._host._stop_hooks.append((self._plugin, hook))

    def provide(self, capability_name: str, obj: object) -> None:
        _capabilities[capability_name] = obj
        self._host._provided.append((capability_name, obj))


class PluginHost:
    """The plug-ins of one service: what they attached, and its start and stop."""

    def __init__(self, service_name: str) -> None:
        self.service_name = service_name
        self.loaded: list[str] = []
        self.failures: list[PluginFailure] = []
        self._unsubscribes: list[Callable[[], None]] = []
        self._app_hooks: list[tuple[str, Callable[[Any], None]]] = []
        self._start_hooks: list[tuple[str, StartHook]] = []
        self._stop_hooks: list[tuple[str, StopHook]] = []
        self._provided: list[tuple[str, object]] = []

    def install(self, app: FastAPI) -> None:
        """Add the plug-ins' middleware to ``app``. Call it where the outermost middleware belongs."""
        for plugin, hook in self._app_hooks:
            try:
                hook(app)
            except Exception as exc:
                self._failed(plugin, "install", exc)

    async def start(self, *, config_service: Any, logger: Any) -> None:  # noqa: ANN401 - the service's own objects
        """Run the start hooks in registration order. A hook that raises is logged, and the rest still run."""
        runtime = ServiceRuntime(self.service_name, config_service, logger)
        for plugin, hook in self._start_hooks:
            try:
                await hook(runtime)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._failed(plugin, "start", exc)
        collection = get_capability(USAGE_COLLECTION)
        described = getattr(collection, "describe", None)
        logger.info(
            "Plug-ins loaded for %s: %s. Usage collection: %s",
            self.service_name,
            ", ".join(self.loaded) or "none",
            described() if callable(described) else "not installed; nothing is collected",
        )

    async def stop(self) -> None:
        """Run the stop hooks in reverse order. Event subscriptions stay: see ``close``."""
        for plugin, hook in reversed(self._stop_hooks):
            try:
                await hook()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._failed(plugin, "stop", exc)

    def close(self) -> None:
        """End this host's event subscriptions and withdraw what its plug-ins provide."""
        for unsubscribe in reversed(self._unsubscribes):
            unsubscribe()
        self._unsubscribes.clear()
        for name, obj in self._provided:
            if _capabilities.get(name) is obj:
                del _capabilities[name]
        self._provided.clear()

    def _failed(self, plugin: str, stage: str, exc: BaseException) -> None:
        self.failures.append(PluginFailure(plugin, stage, f"{type(exc).__name__}: {exc}"))
        _log.warning("Plug-in %s failed at %s and was skipped: %s: %s", plugin, stage, type(exc).__name__, exc)


def load_plugins(service_name: str, manifest: Sequence[str] | None = None) -> PluginHost:
    """Import every module the manifest names and call its ``plugin.register``.

    ``manifest`` defaults to ``app.plugins_manifest.PLUGINS``. Never raises for
    a plug-in: one that fails to import or register is in ``host.failures``.
    """
    if manifest is None:
        from app.plugins_manifest import PLUGINS

        manifest = PLUGINS
    host = PluginHost(service_name)
    for module_path in manifest:
        try:
            plugin = importlib.import_module(module_path).plugin
        except Exception as exc:
            host._failed(module_path, "import", exc)
            continue
        name = getattr(plugin, "name", module_path)
        try:
            plugin.register(PluginContext(host, name))
        except Exception as exc:
            host._failed(name, "register", exc)
            continue
        host.loaded.append(name)
    return host


def reset() -> None:
    """Forget every provided capability. For tests."""
    _capabilities.clear()


__all__ = [
    "USAGE_COLLECTION",
    "Plugin",
    "PluginContext",
    "PluginFailure",
    "PluginHost",
    "ServiceRuntime",
    "get_capability",
    "load_plugins",
    "reset",
]
