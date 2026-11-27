"""PipesHub configuration for the egress proxy (P5.4): endpoints, upstreams and the broker's wiring.

Addresses come from ``/services/endpoints`` like every other service URL;
the broker's Docker-side settings come from its environment, next to
``SANDBOX_EGRESS_NETWORK`` which it already reads.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Final, Protocol, override, runtime_checkable

from app.config.constants.service import DefaultEndpoints
from app.harness.credential_roles import worker_client_auth
from app.harness.sandbox.binding_proof import BindingAttester
from app.harness.sandbox.client import configured_endpoint
from app.harness.sandbox.docker_egress import DockerEgressWiring
from app.utils.env_flags import (
    AGENT_HARNESS_KILL_SWITCH,
    EGRESS_CONNECT_TUNNELS,
    env_flag,
)
from harness_core.sandbox.egress.addresses import SubnetPool
from harness_core.sandbox.egress.client import RemoteEgressBindings
from harness_core.sandbox.egress.control import (
    ENV_SANDBOX_SUBNETS,
    sandbox_subnet_pool_from_env,
)
from harness_core.sandbox.egress.internals import internal_networks

if TYPE_CHECKING:
    import asyncio

    from app.config.configuration_service import ConfigurationService
    from app.harness.sandbox.docker_client import DockerClientProvider
    from harness_core.clock import Clock
    from harness_core.sandbox.auth import BrokerAuth
    from harness_core.sandbox.egress.bindings import InMemoryEgressBindings
    from harness_core.sandbox.egress.policy import Network
    from harness_core.sandbox.egress.ports import HostResolver

# Unset: no proxy, and the broker refuses egress allowlists (``egress_proxy_missing``).
ENV_PROXY_CONTAINER = "SANDBOX_EGRESS_PROXY_CONTAINER"
ENV_SUBNETS = ENV_SANDBOX_SUBNETS
ENV_BLOCKED_CIDRS = "EGRESS_BLOCKED_CIDRS"
# Gateway host names as sandboxes use them → the ``/services/endpoints`` entry holding the upstream.
GATEWAYS = {"model-gateway": "modelGateway", "tool-gateway": "toolGateway"}


async def control_endpoint(config_service: ConfigurationService) -> str:
    # Read on every call and never cached: under an outage the fallback refuses at connect, as the outage would.
    found = await configured_endpoint(config_service, "egressProxy", "controlEndpoint", raise_on_error=False)
    return found or DefaultEndpoints.EGRESS_PROXY_CONTROL_ENDPOINT.value


async def gateway_upstreams(config_service: ConfigurationService) -> dict[str, str]:
    """Where the proxy forwards each gateway's traffic; a gateway not configured yet is simply absent.

    An unreadable store raises, so a refresh keeps the table instead of emptying it."""
    upstreams: dict[str, str] = {}
    for host, service in GATEWAYS.items():
        url = await configured_endpoint(config_service, service, raise_on_error=True)
        if url is not None:
            upstreams[host] = url
    return upstreams


async def internal_hosts(config_service: ConfigurationService, upstreams: dict[str, str]) -> tuple[str, ...]:
    """G-21/G-38: PipesHub's services by the names this proxy is configured with: the gateways, Node's internal API,
    the broker and the proxy's own control plane, and the KV stores. Their addresses are never a destination."""
    # Imported here: ``app.harness.inline`` pulls in the run assembly, which the proxy container cannot load.
    from app.harness.inline import node_state_url
    endpoints = [
        await configured_endpoint(config_service, "sandboxBroker", raise_on_error=False),
        await configured_endpoint(config_service, "egressProxy", "controlEndpoint", raise_on_error=False),
    ]
    named = [*upstreams.values(), node_state_url(), *(e for e in endpoints if e)]
    # The KV stores the proxy shares a network with (Compose's sandbox_kv).
    kv = (os.getenv("REDIS_HOST", ""), os.getenv("ETCD_URL", ""))
    named.extend(value.strip() for value in kv if value.strip())
    return tuple(dict.fromkeys(named))


async def pipeshub_internal_networks(
    config_service: ConfigurationService, upstreams: dict[str, str], resolver: HostResolver
) -> tuple[Network, ...]:
    """What no private entry opens: this proxy and the Docker host, the services above, both sandbox pools."""
    # Imported here: the legacy sandbox package's ``__init__`` loads the whole coding sandbox.
    from app.agent_loop_lib.sandbox.coding.egress_firewall import open_egress_pool
    return await internal_networks(
        await internal_hosts(config_service, upstreams),
        resolver,
        (sandbox_subnet_pool_from_env(), str(open_egress_pool())),
    )


class ConfiguredEgressBindings(RemoteEgressBindings):
    """The proxy's control plane at the address configured now, read on every call.

    The broker can start before Node has written ``egressProxy.controlEndpoint``;
    a value read once would leave it on the fallback for its whole life.
    """

    def __init__(self, config_service: ConfigurationService, auth: BrokerAuth) -> None:
        super().__init__(DefaultEndpoints.EGRESS_PROXY_CONTROL_ENDPOINT.value, auth)
        self._config_service = config_service

    @override
    async def _call(self, method: str, path: str, *, json: object = None) -> object:
        self._base = (await control_endpoint(self._config_service)).rstrip("/")
        return await super()._call(method, path, json=json)


async def broker_egress_wiring(
    config_service: ConfigurationService, *, provider: DockerClientProvider, clock: Clock, network_prefix: str
) -> DockerEgressWiring | None:
    container = os.getenv(ENV_PROXY_CONTAINER)
    if not container:
        return None
    bindings = ConfiguredEgressBindings(config_service, worker_client_auth(config_service))
    return DockerEgressWiring(
        provider=provider,
        clock=clock,
        proxy_container=container,
        bindings=bindings,
        pool=SubnetPool(sandbox_subnet_pool_from_env()),
        network_prefix=network_prefix,
        attester=BindingAttester.from_env(),
    )


@runtime_checkable
class EgressStopsFeed(Protocol):
    """Keeps the operator stops the proxy holds current while it runs (ADR-OPS-01 D4.3): an extension point, read
    with ``harness_edition().port(EgressStopsFeed, NO_EGRESS_STOPS)``; this build's feed holds none. Returns once
    ``stopped`` is set."""

    async def run(
        self, config_service: ConfigurationService, bindings: InMemoryEgressBindings, stopped: asyncio.Event
    ) -> None: ...


class NoEgressStops:
    """No operator egress stops: the proxy holds only the deployment's kill switch, which it reads at start-up."""

    async def run(
        self, config_service: ConfigurationService, bindings: InMemoryEgressBindings, stopped: asyncio.Event
    ) -> None:
        del config_service, bindings
        _ = await stopped.wait()


NO_EGRESS_STOPS: Final = NoEgressStops()


def connect_tunnels_enabled() -> bool:
    """P5.4b: whether the proxy opens ``CONNECT`` tunnels to allowlisted hosts (``EGRESS_CONNECT_TUNNELS``)."""
    return env_flag(EGRESS_CONNECT_TUNNELS)


def kill_switch_on() -> bool:
    """C6.14: the operator's incident stop (``AGENT_HARNESS_KILL_SWITCH``), which the proxy enforces on sandbox egress:
    no sandbox serving a harness run is bound or restored while it is on."""
    return env_flag(AGENT_HARNESS_KILL_SWITCH)


__all__ = [
    "ENV_BLOCKED_CIDRS",
    "ConfiguredEgressBindings",
    "ENV_PROXY_CONTAINER",
    "ENV_SUBNETS",
    "GATEWAYS",
    "NO_EGRESS_STOPS",
    "EgressStopsFeed",
    "NoEgressStops",
    "broker_egress_wiring",
    "connect_tunnels_enabled",
    "control_endpoint",
    "gateway_upstreams",
    "internal_hosts",
    "kill_switch_on",
    "pipeshub_internal_networks",
]
