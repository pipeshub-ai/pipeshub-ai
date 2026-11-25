"""DE-1..DE-4: how the Docker broker wires a sandbox to the egress proxy (P5.4 §1.1)."""

from __future__ import annotations

import ipaddress
import uuid
from typing import TYPE_CHECKING

import docker
import pytest

from app.harness.sandbox.docker_broker import DockerSandboxBroker
from app.harness.sandbox.docker_policy import HardeningPolicy
from harness_core.ids import RandomIds
from harness_core.sandbox.egress.types import EGRESS_PROXY_PORT
from harness_core.sandbox.types import (
    EgressDestination,
    EgressPolicy,
    LeaseRef,
    MountSpec,
    Resources,
    RuntimeMode,
    SandboxSpec,
    TokenRoute,
)
from harness_core.testing.sandbox.contract import WORK, parse_http, placeholder, run
from tests.support.docker_networks import create_on_free_subnet
from tests.unit.agent_loop_lib.sandbox.contract.conftest import DOCKER_TEST_IMAGE
from tests.unit.harness.sandbox_broker.conftest import EGRESS_TEST_POOL, egress_wiring
from tests.unit.harness.sandbox_broker.egress_stack import OWNER, sandbox_curl

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.harness.sandbox.docker_client import DockerClientProvider
    from harness_core.sandbox.egress.client import RemoteEgressBindings
    from harness_core.testing.clock import ManualClock
    from tests.unit.harness.sandbox_broker.egress_stack import EgressStack

LEASE = LeaseRef("worker-de", 1)
MODEL = f"http://model-gateway:{EGRESS_PROXY_PORT}/v1/ping"


def _spec(ph: str, mode: RuntimeMode = RuntimeMode.DOCKER_HARDENED) -> SandboxSpec:
    return SandboxSpec(
        image_digest=DOCKER_TEST_IMAGE,
        mode=mode,
        resources=Resources(cpus=0.5, memory_mb=256, pids=64, scratch_mb=64),
        egress=EgressPolicy(
            destinations=(EgressDestination("model-gateway", (EGRESS_PROXY_PORT,)),),
            token_routes=(TokenRoute(ph, "model-gateway", "gateway:model"),),
        ),
        mounts=(MountSpec(WORK),),
        labels={**OWNER, "runId": "run-de"},
    )


async def _http(handle: object, ph: str) -> tuple[int, str]:
    process = await handle.exec(sandbox_curl(MODEL, {"Authorization": f"Bearer {ph}"}, via_proxy=False), env={})  # type: ignore[attr-defined]
    out, _, _ = await run(process, timeout_s=60)
    return parse_http(out)[:2]


pytestmark = [pytest.mark.docker, pytest.mark.timeout(600, method="thread")]


@pytest.fixture
def runtime_mode() -> RuntimeMode:
    return RuntimeMode.DOCKER_HARDENED


def test_de1_the_hardening_policy_accepts_only_broker_owned_networks() -> None:
    policy = HardeningPolicy(
        user="1000:1000",
        allowed_networks=frozenset({"legacy-egress"}),
        volume_allowed=lambda _: True,
        network_allowed=lambda name: name.startswith("pxh-p54-x-net-"),
    )
    assert policy.network_ok("none") and policy.network_ok("legacy-egress") and policy.network_ok("pxh-p54-x-net-sbx_1")
    assert not policy.network_ok("bridge") and not policy.network_ok("host") and not policy.network_ok("pxh-other")


async def test_de1_a_sandbox_gets_its_own_isolated_network_and_the_proxy_its_address(
    egress_docker_broker: DockerSandboxBroker,
    provider: DockerClientProvider,
    egress_stack: EgressStack,
    egress_bindings: RemoteEgressBindings,
) -> None:
    ph = placeholder()
    handle = await egress_docker_broker.acquire(_spec(ph), lease=LEASE, idempotency_key=f"k-de1-{ph}")
    api = provider.client.api
    try:
        container = api.inspect_container(handle.container_id)
        network_name = container["HostConfig"]["NetworkMode"]
        assert egress_docker_broker.egress is not None and network_name == egress_docker_broker.egress.network_name(handle.id)
        network = api.inspect_network(network_name)
        assert network["Internal"] is True
        assert network["Options"]["com.docker.network.bridge.gateway_mode_ipv4"] == "isolated"
        subnet = ipaddress.ip_network(network["IPAM"]["Config"][0]["Subnet"])
        assert subnet.prefixlen == 29 and subnet.subnet_of(ipaddress.ip_network(EGRESS_TEST_POOL))
        sandbox_ip = container["NetworkSettings"]["Networks"][network_name]["IPAddress"]
        proxy_ip = api.inspect_container(egress_stack.proxy)["NetworkSettings"]["Networks"][network_name]["IPAddress"]
        assert (sandbox_ip, proxy_ip) == (str(subnet[3]), str(subnet[2]))
        assert sorted(container["HostConfig"]["ExtraHosts"]) == sorted([f"egress-proxy:{proxy_ip}", f"model-gateway:{proxy_ip}"])
        assert list(container["NetworkSettings"]["Networks"]) == [network_name]
        assert handle.id in await egress_bindings.bound()
        assert await _http(handle, ph) == (200, "-")
    finally:
        await egress_docker_broker.release(handle)


async def test_de2_release_and_reap_leave_no_network_endpoint_or_binding(
    egress_docker_broker: DockerSandboxBroker,
    provider: DockerClientProvider,
    egress_stack: EgressStack,
    egress_bindings: RemoteEgressBindings,
) -> None:
    api = provider.client.api
    wiring = egress_docker_broker.egress
    assert wiring is not None
    ph = placeholder()
    handle = await egress_docker_broker.acquire(_spec(ph), lease=LEASE, idempotency_key=f"k-de2-{ph}")
    name = wiring.network_name(handle.id)
    await egress_docker_broker.release(handle)
    assert not api.networks(names=[name])
    assert name not in api.inspect_container(egress_stack.proxy)["NetworkSettings"]["Networks"]
    assert handle.id not in await egress_bindings.bound()
    # A broker that died between the network and the container leaves an orphan the reaper removes.
    orphan_labels = {**OWNER, "runId": "run-de2-orphan"}
    spec = _spec(placeholder())
    labels = {**orphan_labels, wiring.labels.managed: "1", wiring.labels.sandbox_id: "sbx_orphan_de2", wiring.labels.created: "0"}
    _ = await wiring.attach("sbx_orphan_de2", spec, labels)
    assert "sbx_orphan_de2" in await egress_bindings.bound()
    assert await egress_docker_broker.reap(labels={"runId": "run-de2-orphan"}, older_than_s=0) == 0
    assert not api.networks(names=[wiring.network_name("sbx_orphan_de2")])
    assert "sbx_orphan_de2" not in await egress_bindings.bound()


async def test_de3_a_restarted_broker_and_a_restarted_proxy_get_their_bindings_back(
    egress_docker_broker: DockerSandboxBroker,
    provider: DockerClientProvider,
    clock: ManualClock,
    egress_stack: EgressStack,
    egress_bindings: RemoteEgressBindings,
    egress_namespace: str,
    egress_prefix: str,
) -> None:
    ph = placeholder()
    key = f"k-de3-{ph}"
    handle = await egress_docker_broker.acquire(_spec(ph), lease=LEASE, idempotency_key=key)
    try:
        await egress_bindings.unregister(handle.id)
        assert await _http(handle, ph) == (403, "unbound_source")
        restarted = await DockerSandboxBroker.create(
            provider=provider,
            clock=clock,
            ids=RandomIds(),
            allowed_images=frozenset({DOCKER_TEST_IMAGE}),
            label_namespace=egress_namespace,
            volume_prefix=egress_prefix,
            egress=egress_wiring(provider, clock, egress_stack, egress_bindings, egress_prefix),
        )
        adopted = await restarted.acquire(_spec(ph), lease=LeaseRef("worker-de", 2), idempotency_key=key)
        assert adopted.id == handle.id
        assert await _http(adopted, ph) == (200, "-")

        assert await restarted.resync_egress()
        egress_stack.restart_proxy()
        assert await _http(adopted, ph) == (403, "unbound_source")
        assert await restarted.resync_egress()
        assert await _http(adopted, ph) == (200, "-")
        assert not await restarted.resync_egress()
        await restarted.release(adopted)
    finally:
        await egress_docker_broker.release(handle)


async def test_de4_a_subnet_taken_by_another_network_is_skipped(
    egress_docker_broker: DockerSandboxBroker, provider: DockerClientProvider
) -> None:
    wiring = egress_docker_broker.egress
    assert wiring is not None
    api = provider.client.api
    # Other lanes on this shared daemon create and remove networks between our list and our inspect or create.
    taken: set[str] = set()
    for n in api.networks():
        try:
            configs = (api.inspect_network(n["Id"]).get("IPAM") or {}).get("Config") or []
        except docker.errors.NotFound:
            continue
        taken |= {c["Subnet"] for c in configs if c.get("Subnet") and wiring.pool.contains(c["Subnet"])}

    def free_subnets() -> Iterator[str]:
        while (subnet := wiring.pool.allocate(taken)) is not None:
            taken.add(subnet.cidr)
            yield subnet.cidr

    squat = f"pxh-p54-squat-{uuid.uuid4().hex[:8]}"
    squatted = create_on_free_subnet(provider.client, squat, free_subnets(), labels=OWNER, internal=True, attempts=8)
    ph = placeholder()
    try:
        handle = await egress_docker_broker.acquire(_spec(ph), lease=LEASE, idempotency_key=f"k-de4-{ph}")
        try:
            network = api.inspect_network(wiring.network_name(handle.id))
            assert network["IPAM"]["Config"][0]["Subnet"] != squatted
            assert await _http(handle, ph) == (200, "-")
        finally:
            await egress_docker_broker.release(handle)
    finally:
        api.remove_network(squat)
