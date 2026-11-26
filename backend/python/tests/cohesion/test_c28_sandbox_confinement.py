"""C-28: a sandbox reaches nothing but its egress proxy's port, without relying on proxy settings.

C-13 proves the gateways are reached only through the proxy. This proves nothing else leaves: inside a sandbox the
broker acquires with the engine controller's egress shape (the two gateways, one placeholder each), for every engine
image on the host, a ``bash`` probe unsets every proxy variable and dials directly (``tests/support/sandbox_confinement``):
public addresses, DNS over UDP and TCP, the cloud metadata addresses (IPv4, IPv6, Azure's wire server), the Docker
host gateway, the upstream bridge where the gateways and Node listen, another container, and every address of the
sandbox's own network on a port list. Only the proxy's data port may answer.

Negative control: the same probe from a container on the upstream bridge does reach the bridge's listener, so a probe
that can never connect cannot pass.
"""

from __future__ import annotations

import contextlib
import uuid
from typing import TYPE_CHECKING, Any

import pytest

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
from tests.support.sandbox_confinement import (
    Confinement,
    ProbeReport,
    assert_confined,
    probe,
    sandbox_network,
    script,
)
from tests.unit.harness.engines.real_stack import (
    ALLOWLIST,
    HELPER_IMAGE,
    OWNER,
    engine_broker,
    lane_available,
    start_stack,
    stop_stack,
)
from tests.unit.harness.sandbox_broker.conftest import cleanup, cleanup_networks

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from tests.unit.harness.engines.real_stack import RealStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600, method="thread")]

ENGINE_IMAGES = {e["kind"]: str(e["image"]) for e in ALLOWLIST["engines"]}
CANARY_PORT = 8080
CANARY = "import http.server as h; h.HTTPServer(('0.0.0.0', 8080), h.SimpleHTTPRequestHandler).serve_forever()"


@pytest.fixture(scope="module")
def rig() -> Iterator[tuple[RealStack, dict[str, Any]]]:
    """The proxy stack, and a canary container on Docker's default bridge (another tenant's service)."""
    available, reason = lane_available(HELPER_IMAGE)
    if not available:
        pytest.skip(reason)
    stack = start_stack()
    client = stack.client
    canary = client.containers.run(
        HELPER_IMAGE,
        ["python", "-c", CANARY],
        name=f"pxh-c28-canary-{stack.suffix}",
        labels={**OWNER, "pipeshub.agent": "C-28", "pxh.lane": stack.suffix},
        detach=True,
        user="1000:1000",
    )
    try:
        canary.reload()
        yield stack, {"canary": canary.attrs["NetworkSettings"]["Networks"]["bridge"]["IPAddress"]}
    finally:
        with contextlib.suppress(Exception):
            canary.remove(force=True)
        stop_stack(stack)


def _spec(image: str, tools_upstream: str) -> SandboxSpec:
    return SandboxSpec(
        image_digest=image,
        mode=RuntimeMode.DOCKER_HARDENED,
        resources=Resources(cpus=0.5, memory_mb=256, pids=256, scratch_mb=64),
        egress=EgressPolicy(
            destinations=(
                EgressDestination("model-gateway", (8097,)),
                EgressDestination("tool-gateway", (8097,), upstream=tools_upstream),
            ),
            token_routes=(
                TokenRoute(f"pxh-model-{uuid.uuid4().hex}", "model-gateway", "gateway:model"),
                TokenRoute(f"pxh-tools-{uuid.uuid4().hex}", "tool-gateway", "gateway:tools"),
            ),
        ),
        mounts=(MountSpec("/workspace"),),
        labels={**OWNER, "pipeshub.agent": "C-28", "runId": "c28", "attemptId": "c28.a1"},
    )


def _forbidden(stack: RealStack, extra: dict[str, Any]) -> tuple[tuple[str, int], ...]:
    proxy = stack.client.api.inspect_container(stack.proxy)["NetworkSettings"]["Networks"][stack.network]["IPAddress"]
    return (
        (stack.host_ip, stack.model_port),
        (stack.host_ip, stack.gateway_port),
        (proxy, 8097),
        (proxy, 8098),
        (extra["canary"], CANARY_PORT),
    )


@pytest.fixture
async def serving(rig: tuple[RealStack, dict[str, Any]]) -> AsyncIterator[tuple[RealStack, dict[str, Any]]]:
    stack, extra = rig
    async with stack.serving():
        yield stack, extra


@pytest.mark.parametrize("engine", sorted(ENGINE_IMAGES))
async def test_c28_an_engine_sandbox_reaches_only_its_proxy_port(
    serving: tuple[RealStack, dict[str, Any]], engine: str
) -> None:
    stack, extra = serving
    image = ENGINE_IMAGES[engine]
    available, reason = lane_available(image)
    if not available:
        pytest.skip(reason)
    namespace = f"io.pipeshub.c28.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-c28-" + namespace.rsplit(".", 1)[1]
    broker, provider = await engine_broker(stack, namespace, prefix, image=image)
    try:
        handle = await broker.acquire(
            _spec(image, f"{stack.gateway_base}/unmounted"), lease=LeaseRef("worker-c28", 1), idempotency_key=prefix
        )
        container = stack.client.api.inspect_container(broker.live(handle.id).container_id)
        network = sandbox_network(container)
        proxy_address = _proxy_address(stack, container)
        confinement = Confinement(proxy_address, forbidden=_forbidden(stack, extra))
        report = await probe(handle, confinement.targets(network), http=confinement.http_targets())
        assert_confined(report, confinement, f"{engine} sandbox ({image})")
        # E2E4 EC-6: the control plane has no listener on the sandbox's network at all.
        assert (proxy_address, 8098) in report.closed_tcp(), report.lines
        assert "destination_not_allowed" in report.http(proxy_address, 8097), report.lines
        assert {"UDP53 127.0.0.11", "TCP53 127.0.0.11"} <= set(report.dns_refusals()), report.lines
        await broker.release(handle)
    finally:
        await broker.aclose()
        await provider.run_blocking(cleanup, provider, namespace, prefix)
        await provider.run_blocking(cleanup_networks, provider, namespace, stack.proxy)
        provider.close()


async def test_c28_negative_control_the_probe_connects_where_a_route_exists(
    serving: tuple[RealStack, dict[str, Any]],
) -> None:
    """From a container on the upstream bridge the probe reaches the bridge's listener and the canary is a closed
    door only because it is on another bridge: open is reported as open."""
    stack, extra = serving
    targets = [(stack.host_ip, stack.model_port), (extra["canary"], CANARY_PORT)]
    container = stack.client.containers.run(
        "pxh-sandbox:pr1" if lane_available("pxh-sandbox:pr1")[0] else HELPER_IMAGE,
        ["bash", "-c", script(targets)],
        network=stack.network,
        labels={**OWNER, "pipeshub.agent": "C-28", "pxh.lane": stack.suffix},
        detach=True,
    )
    try:
        container.wait(timeout=120)
        lines = tuple(line.strip() for line in container.logs().decode().splitlines() if line.strip())
    finally:
        container.remove(force=True)
    report = ProbeReport(lines, tuple(targets))
    assert report.complete, lines
    assert (stack.host_ip, stack.model_port) in report.open_tcp(), lines


def _proxy_address(stack: RealStack, sandbox: dict[str, Any]) -> str:
    """The proxy's address on the sandbox's own network (the broker attaches the proxy to it)."""
    (name,) = sandbox["NetworkSettings"]["Networks"]
    proxy = stack.client.api.inspect_container(stack.proxy)
    return str(proxy["NetworkSettings"]["Networks"][name]["IPAddress"])
