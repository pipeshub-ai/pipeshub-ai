"""The telemetry plug-in: usage metrics and events for the collector, and the ops ``/metrics`` port.

Nothing outside this package imports it; ``app.plugins_manifest`` names it and
``app.plugins.load_plugins`` calls ``plugin.register``. It listens to platform
events (``listeners``) and turns each into the metric observation or event
record its call site used to make. A build without telemetry deletes this
package and its manifest line.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.plugins import USAGE_COLLECTION, PluginContext, ServiceRuntime

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastapi import FastAPI

    from app.plugins.telemetry.ops_exporter import OpsExporter
    from app.plugins.telemetry.pusher import MetricsPusher


class UsageCollection:
    """What ``get_capability("usage_collection")`` answers while this plug-in is loaded."""

    def __init__(self, *, enabled: bool) -> None:
        self.enabled = enabled

    def describe(self) -> str:
        from app.plugins.telemetry.flag import TELEMETRY_COLLECTION_FLAG

        if not self.enabled:
            return f"installed, switched off by {TELEMETRY_COLLECTION_FLAG}; nothing is collected"
        return "installed and on; sent to the collector while the install's metric collection setting allows it"


class TelemetryPlugin:
    name = "telemetry"

    def register(self, ctx: PluginContext) -> None:
        from app.plugins.telemetry import harness_capabilities, listeners
        from app.plugins.telemetry.flag import telemetry_enabled
        from app.plugins.telemetry.pusher import MetricsPusher

        enabled = telemetry_enabled()
        service_name = ctx.service_name
        ctx.provide(USAGE_COLLECTION, UsageCollection(enabled=enabled))
        if enabled:
            from app.plugins.telemetry.middleware import MetricsMiddleware

            ctx.add_asgi_middleware(MetricsMiddleware, service_name=service_name)
            listeners.subscribe(ctx.on)

        pushers: list[MetricsPusher] = []

        async def start_pusher(runtime: ServiceRuntime) -> None:
            pusher = MetricsPusher(runtime.config_service, service_name, runtime.logger)
            pushers.append(pusher)
            await pusher.start()  # logs and does nothing when the flag is off

        async def stop_pusher() -> None:
            while pushers:
                await pushers.pop().stop()

        ctx.on_start(start_pusher)
        ctx.on_stop(stop_pusher)
        # Whatever the collection flag says, as before: the harness's ops metrics follow the backend the flag picks.
        harness_capabilities.provide(ctx)
        _OpsExporterWiring(service_name.removesuffix("_service")).attach(ctx)


class _OpsExporterWiring:
    """Runs the ops exporter for as long as the service's resource governor runs.

    The exporter has its own switch (``PIPESHUB_METRICS_ENABLED``), so it is wired
    whatever the collection flag says. The governor is read from ``app.state``,
    where the service keeps it; an event never carries the object.
    """

    def __init__(self, service: str) -> None:
        self._service = service
        self._app: FastAPI | None = None
        self._exporter: OpsExporter | None = None

    def attach(self, ctx: PluginContext) -> None:
        ctx.on_app(self._keep_app)
        ctx.on("resource_governor.started", self._started)
        ctx.on("resource_governor.stopped", self._stopped)

    def _keep_app(self, app: FastAPI) -> None:
        self._app = app

    def _started(self, _name: str, _payload: Mapping[str, Any]) -> None:
        from app.plugins.telemetry.ops_exporter import exporter_from_env

        governor = getattr(getattr(self._app, "state", None), "governor", None)
        if governor is None or self._exporter is not None:
            return
        self._exporter = exporter_from_env(governor, self._service)
        if self._exporter is not None:
            self._exporter.start()

    def _stopped(self, _name: str, _payload: Mapping[str, Any]) -> None:
        # Bounded (a join of at most STOP_JOIN_S and one snapshot write), at shutdown only, and
        # what the service did inline at this point: the last snapshot must be folded into the
        # carry before the process leaves.
        exporter, self._exporter = self._exporter, None
        if exporter is not None:
            exporter.stop()


plugin = TelemetryPlugin()
