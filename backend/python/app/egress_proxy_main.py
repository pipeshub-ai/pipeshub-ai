"""Egress proxy process role (P5.4): the only way out of a sandbox with an allowlist.

Run with ``python -m app.egress_proxy_main``. Two listeners:

- the data plane on ``EGRESS_PROXY_PORT`` (8097), which sandboxes reach on
  their own networks; no credential, the connection's addresses name the
  sandbox;
- the control plane on ``EGRESS_CONTROL_PORT`` (8098), where the sandbox
  broker registers bindings, bound only to the addresses ``EGRESS_CONTROL_HOST``
  names (never a wildcard, never inside ``SANDBOX_EGRESS_SUBNETS``), so no
  sandbox has a network path to it; every route but ``/healthz`` requires the
  worker credential Node provisions, and a request arriving on a sandbox's
  network is refused as well.

Gateway upstreams come from ``/services/endpoints`` (``modelGateway``,
``toolGateway``), re-read every ``UPSTREAM_REFRESH_S``: in Compose this
process starts before Node writes them. PipesHub's own addresses (G-21, G-38:
this proxy, the Docker host, the gateways, Node, the broker, the KV stores, the
sandbox pools) are re-read with them and refused as destinations whatever
``EGRESS_PRIVATE_ALLOW`` lists; ``EGRESS_UPSTREAM_PROXY`` chains every public
destination through the deployment's forward proxy (``upstream_proxy.py``). A run's own tool endpoint is the
sandbox's ``EgressDestination.upstream`` (E0). Tokens are Node's gateway tokens
for the sandbox's run attempt (``NodeGatewayTokenSource``), which both
gateways verify.

C6.12: what each run's sandboxes move is counted per destination alias (``volume.py``), exported on this process's
metrics (``app.telemetry``, pushed like every service's), and an unusual volume is journaled on the run; the
bounds and the optional hard cap are ``EGRESS_VOLUME_*`` and ``EGRESS_RUN_MAX_MB``, and a malformed one stops the
proxy here. Counts and org baselines are kept in the durable state with the bindings.

ADR-OPS-01 D4.3: operator egress stops are kept current by the ``EgressStopsFeed`` (this build's, ``NO_EGRESS_STOPS``,
holds none); a held stop revokes the run bindings it covers and refuses new ones. The start-up
``AGENT_HARNESS_KILL_SWITCH`` holds whatever a feed says.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from typing import TYPE_CHECKING, cast

import app.utils.runtime_threads  # noqa: F401 - must precede all ML library imports
from app import edition_services
from app.core.org_context import spawn_detached
from app.edition import check_runtime_settings
from app.harness.access.refusals import NodeRefusalSink
from app.harness.credential_roles import (
    await_component_document,
    listener_auth,
    process_holder,
)
from app.harness.gateway.tokens import NodeGatewayTokens, NodeGatewayTokenSource
from app.harness.inline import node_state_url
from app.harness.sandbox.egress_config import (
    ENV_BLOCKED_CIDRS,
    NO_EGRESS_STOPS,
    EgressStopsFeed,
    connect_tunnels_enabled,
    gateway_upstreams,
    kill_switch_on,
    pipeshub_internal_networks,
)
from app.harness.sandbox.egress_credentials import sealed_token_source
from app.harness.sandbox.egress_resolver import PublicHostResolver
from app.harness.sandbox.egress_state import durable_state, egress_sealers
from app.telemetry.backend import METRICS_BACKEND
from app.telemetry.modules.egress_metrics import PrometheusVolumeMetrics
from app.telemetry.pusher import MetricsPusher
from app.utils.env_flags import validate_env_flags
from harness_core.clock import SystemClock
from harness_core.gateway.refusal_reports import RefusalReporter
from harness_core.ids import RandomIds
from harness_core.logs import HarnessLog
from harness_core.sandbox.egress.bindings import InMemoryEgressBindings
from harness_core.sandbox.egress.control import control_bind_addresses_from_env
from harness_core.sandbox.egress.interception import load_authority
from harness_core.sandbox.egress.policy import POSTGRES_PORT, parse_networks
from harness_core.sandbox.egress.proxy import EgressProxy, ProxyLimits
from harness_core.sandbox.egress.resolver import SystemResolver
from harness_core.sandbox.egress.sealed import RoutedTokenSource, SealedTokenSource
from harness_core.sandbox.egress.server import EgressServer
from harness_core.sandbox.egress.state import DurableEgressBindings, keep_state
from harness_core.sandbox.egress.types import EGRESS_CONTROL_PORT, EGRESS_PROXY_PORT
from harness_core.sandbox.egress.upstream_proxy import upstream_egress_from_env
from harness_core.sandbox.egress.volume import EgressVolume, volume_limits_from_env

# Stop at load on a setting this edition does not run.
check_runtime_settings()

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService
    from harness_core.clock import Clock
    from harness_core.ids import IdSource
    from harness_core.sandbox.egress.ports import EgressStateStore
    from harness_core.sandbox.egress.state import StateSealer

_LOG = HarnessLog.named("app.egress_proxy")
UPSTREAM_REFRESH_S = 30.0
TELEMETRY_SERVICE = "egress_proxy"


async def create_service(
    config_service: ConfigurationService,
    *,
    clock: Clock,
    ids: IdSource,
    state: EgressStateStore | None = None,
    sealer: StateSealer | None = None,
) -> EgressServer:
    """The proxy; with ``state`` and ``sealer`` its bindings and grants are restored before it serves (P5.4b)."""
    upstreams = await gateway_upstreams(config_service)
    # A malformed upstream egress setting stops the proxy here rather than opening less, or more, than was meant.
    upstream_egress = upstream_egress_from_env()
    volume_limits = volume_limits_from_env()
    resolver = PublicHostResolver()
    instance = ids.new_id("egress")
    # The incident stop reaches running work here: no sandbox serving a harness run is bound or restored (C6.14).
    kill_switch = kill_switch_on()
    bindings: InMemoryEgressBindings
    if state is not None and sealer is not None:
        durable = DurableEgressBindings(state, sealer, clock, kill_switch=kill_switch)
        instance = (await durable.load(instance)).instance
        bindings = durable
    else:
        bindings = InMemoryEgressBindings(kill_switch=kill_switch)
    if kill_switch:
        _LOG.warning("egress.kill_switch_on", reasonCode="kill_switch")
    sealed = await proxy_sealed_tokens(config_service, clock)
    proxy = EgressProxy(
        bindings=bindings,
        tokens=RoutedTokenSource(
            NodeGatewayTokenSource(
                NodeGatewayTokens(base_url=node_state_url(), credentials=process_holder(config_service))
            ),
            sealed,
        ),
        resolver=resolver,
        upstreams=upstreams,
        clock=clock,
        ids=ids,
        limits=ProxyLimits(
            blocked_networks=parse_networks((os.getenv(ENV_BLOCKED_CIDRS) or "").split(",")),
            connect_tunnels=connect_tunnels_enabled(),
            # The proxy ends a sandbox's TLS with the deployment's one authority: to put a credential PipesHub seals on
            # its HTTPS (OWD-1, WP-06), and to check every request to an allowed website (WP-63).
            intercepts=True,
        ),
        refusals=RefusalReporter(NodeRefusalSink(base_url=node_state_url())),
        upstream_egress=upstream_egress,
        private_resolver=PublicHostResolver(allow_private=True),
        internal_networks=await pipeshub_internal_networks(config_service, upstreams, SystemResolver()),
        volume=EgressVolume(
            volume_limits,
            clock=clock,
            bindings=bindings,
            metrics=PrometheusVolumeMetrics(),
            store=state if sealer is not None else None,
            sealer=sealer if state is not None else None,
        ),
    )
    # SEC-C-10: the worker's and the broker's credentials on the control routes, the scraper's on /metrics.
    verifier = listener_auth(config_service, "egress-proxy")
    authority = await load_authority(clock, state, sealer)
    # C9.04: this process's registry for Node's control-plane metrics route, under the scraper's credential.
    return EgressServer(proxy, verifier, instance=instance, authority=authority, metrics=METRICS_BACKEND.serialize)


async def proxy_sealed_tokens(config_service: ConfigurationService, clock: Clock) -> SealedTokenSource | None:
    """The opener of credentials sealed into routes: under the keys Node sealed for this proxy on the sandbox plane
    (ADR-KEY-02), else under ``SECRET_KEY``; ``None`` without either (sealed routes are then refused, never sent)."""
    _, sealer = await egress_sealers(config_service)
    return SealedTokenSource(sealer, clock) if sealer is not None else sealed_token_source(clock)


async def refresh_upstreams(server: EgressServer, config_service: ConfigurationService) -> bool:
    """Re-read the gateway endpoints; a change is logged once, an unreadable store leaves the table as it is."""
    try:
        upstreams = await gateway_upstreams(config_service)
    except Exception as exc:
        _LOG.warning("egress.upstreams_refresh_failed", errorType=type(exc).__name__)
        return False
    changed = server.proxy.replace_upstreams(upstreams)
    if changed:
        _LOG.info("egress.upstreams_changed", gateways=sorted(upstreams))
    try:
        internals = await pipeshub_internal_networks(config_service, upstreams, SystemResolver())
    except Exception as exc:
        _LOG.warning("egress.internals_refresh_failed", errorType=type(exc).__name__)
        return changed
    if server.proxy.replace_internal_networks(internals):
        _LOG.info("egress.internals_changed", networks=len(internals))
    return changed


async def _refresh_loop(server: EgressServer, config_service: ConfigurationService, stopped: asyncio.Event) -> None:
    while not stopped.is_set():
        with contextlib.suppress(TimeoutError):
            _ = await asyncio.wait_for(stopped.wait(), UPSTREAM_REFRESH_S)
        if not stopped.is_set():
            _ = await refresh_upstreams(server, config_service)


async def run_proxy(data_port: int = EGRESS_PROXY_PORT, control_port: int = EGRESS_CONTROL_PORT) -> None:
    from app.harness.assembly.services import HarnessServices

    control_hosts = control_bind_addresses_from_env()
    validate_env_flags()
    services = HarnessServices()
    config_service = services.config_service()
    _ = await await_component_document(config_service)
    store, sealer = await durable_state(config_service)
    server = await create_service(config_service, clock=SystemClock(), ids=RandomIds(), state=store, sealer=sealer)
    _ = await server.start(
        host="0.0.0.0",
        data_port=data_port,
        control_port=control_port,
        control_host=control_hosts,
        database_port=POSTGRES_PORT,
    )
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signum, stopped.set)
    stops = edition_services.harness_edition().port(EgressStopsFeed, NO_EGRESS_STOPS)
    background = [
        spawn_detached(_refresh_loop(server, config_service, stopped)),
        spawn_detached(stops.run(config_service, server.proxy.bindings, stopped)),
    ]
    if isinstance(server.proxy.bindings, DurableEgressBindings):
        background.append(spawn_detached(keep_state(server.proxy.bindings, stopped)))
    pusher = MetricsPusher(config_service, TELEMETRY_SERVICE, logging.getLogger("app.egress_proxy.telemetry"))
    await pusher.start()
    try:
        _ = await stopped.wait()
    finally:
        _ = await asyncio.gather(*background, return_exceptions=True)
        await pusher.stop()
        await server.stop()
        shutdown = cast("object", services.shutdown_resources())  # pyright: ignore[reportUnknownMemberType]
        if asyncio.iscoroutine(shutdown):
            await shutdown


if __name__ == "__main__":
    asyncio.run(run_proxy())
