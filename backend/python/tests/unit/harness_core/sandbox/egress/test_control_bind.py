"""E2E4 EC: the egress proxy's control plane listens only where no sandbox has a network path.

The proxy is attached to every sandbox's own network, so a control listener on a wildcard address accepts TCP from
every sandbox and only the application refuses it (``control_from_sandbox``, which also misses a network whose
binding is not registered yet). The listener now binds only the addresses ``EGRESS_CONTROL_HOST`` names, never a
wildcard and never an address inside the sandbox subnet pool.
"""

from __future__ import annotations

import asyncio
import contextlib

import aiohttp
import pytest

from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress.addresses import DEFAULT_SUBNET_POOL
from harness_core.sandbox.egress.bindings import InMemoryEgressBindings
from harness_core.sandbox.egress.control import (
    ENV_CONTROL_HOST,
    ENV_SANDBOX_SUBNETS,
    control_bind_addresses,
    control_bind_addresses_from_env,
    sandbox_subnet_pool_from_env,
)
from harness_core.sandbox.egress.errors import ControlPlaneUnconfined
from harness_core.sandbox.egress.proxy import EgressProxy
from harness_core.sandbox.egress.resolver import StaticResolver
from harness_core.sandbox.egress.server import EgressServer
from harness_core.sandbox.egress.tokens import InMemoryTokenSource
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds

POOL = "10.213.0.0/16"
NAMES = {
    "egress-proxy": ("172.30.0.5",),
    "both-planes": ("172.30.0.5", "10.213.0.18"),
    "sandbox-only": ("10.213.0.2",),
    "nowhere": (),
}


def _resolve(name: str) -> tuple[str, ...]:
    return NAMES[name]


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("172.30.0.5", ("172.30.0.5",)),
        ("172.30.0.5, 127.0.0.1", ("172.30.0.5", "127.0.0.1")),
        ("egress-proxy", ("172.30.0.5",)),
        # An address of the proxy's on a sandbox network (a restart while attached) is dropped, the rest kept.
        ("both-planes", ("172.30.0.5",)),
        ("fd00::5", ("fd00::5",)),
    ],
)
def test_ec1_the_addresses_the_control_plane_binds(configured: str, expected: tuple[str, ...]) -> None:
    assert control_bind_addresses(configured, pool=POOL, resolve=_resolve) == expected


@pytest.mark.parametrize("configured", [None, "", " ", "0.0.0.0", "::", "*", "172.30.0.5,0.0.0.0", "sandbox-only", "nowhere", "10.213.4.2"])
def test_ec1_a_wildcard_unset_or_sandbox_facing_control_host_is_refused(configured: str | None) -> None:
    with pytest.raises(ControlPlaneUnconfined) as refused:
        _ = control_bind_addresses(configured, pool=POOL, resolve=_resolve)
    assert refused.value.reason_code == "control_unconfined"


def test_ec4_the_environment_names_the_host_and_the_broker_s_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_CONTROL_HOST, raising=False)
    monkeypatch.delenv(ENV_SANDBOX_SUBNETS, raising=False)
    with pytest.raises(ControlPlaneUnconfined, match=ENV_CONTROL_HOST):
        _ = control_bind_addresses_from_env()
    assert sandbox_subnet_pool_from_env() == DEFAULT_SUBNET_POOL
    monkeypatch.setenv(ENV_SANDBOX_SUBNETS, "10.99.0.0/16")
    monkeypatch.setenv(ENV_CONTROL_HOST, "127.0.0.1,10.99.1.2")
    assert control_bind_addresses_from_env() == ("127.0.0.1",)


def _server() -> EgressServer:
    clock = ManualClock()
    proxy = EgressProxy(
        bindings=InMemoryEgressBindings(),
        tokens=InMemoryTokenSource(b"k" * 32, clock),
        resolver=StaticResolver(),
        upstreams={},
        clock=clock,
        ids=SeededIds(),
    )
    return EgressServer(proxy, StaticBearerAuth("control-token-e2e4"), instance="egress-ec")


async def _connects(host: str, port: int) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 2)
    except OSError:
        return False
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return True


async def test_ec2_the_control_plane_refuses_tcp_on_every_address_it_was_not_given() -> None:
    server = _server()
    data_port, control_port = await server.start(host="127.0.0.1", data_port=0, control_port=0, control_host=("127.0.0.2",))
    try:
        async with aiohttp.ClientSession() as session, session.get(f"http://127.0.0.2:{control_port}/healthz") as live:
            assert live.status == 200
        # The data plane's address (a sandbox's view of the proxy) has no control listener at all.
        assert not await _connects("127.0.0.1", control_port)
        assert await _connects("127.0.0.1", data_port)
    finally:
        await server.stop()


@pytest.mark.parametrize("wildcard", ["0.0.0.0", "::", ""])
async def test_ec2_the_server_never_binds_its_control_plane_to_a_wildcard(wildcard: str) -> None:
    server = _server()
    with pytest.raises(ControlPlaneUnconfined):
        _ = await server.start(host="127.0.0.1", data_port=0, control_port=0, control_host=(wildcard,))
    await server.stop()


async def test_ec2_without_control_hosts_the_server_refuses_a_wildcard_data_host() -> None:
    server = _server()
    with pytest.raises(ControlPlaneUnconfined):
        _ = await server.start(host="0.0.0.0", data_port=0, control_port=0)
    await server.stop()
