"""SEC-EG-13 and SEC-ISO-09 on real Docker (G-21, G-38): the deployment's forward proxy and private host:port entries.

The egress proxy (``egress_entry``, tunnels on) starts with ``EGRESS_UPSTREAM_PROXY`` pointing at a fake corporate
proxy container (``pxh-p54-uproxy-*``) that wants a Basic credential, and with ``EGRESS_PRIVATE_ALLOW`` naming the
stack's target (an "internal Git server" on the rig's private network) and, deliberately, PipesHub's own addresses: a
gateway, the Docker host (the rig gateway) and the proxy itself (by its container name, which the rig's DNS answers). A sandbox from the Docker broker gets an MCP runner's
policy listing every one of those names. The proxy's own resolver (its hosts file) answers the names; the "public"
one answers a documentation address that only the fake corporate proxy ever sees. runc lane on ``pxh-sandbox:x1``.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from harness_core.sandbox.egress.types import EGRESS_CONTROL_PORT
from harness_core.sandbox.mcp_runner.policy import runner_egress_policy
from harness_core.sandbox.mcp_runner.sandboxed import runner_base_env
from harness_core.sandbox.mcp_runner.types import MCPRunnerSpec, RunnerEgressHost
from harness_core.sandbox.types import LeaseRef, MountSpec, Resources, SandboxSpec
from harness_core.testing.sandbox.contract import WORK, run
from tests.unit.agent_loop_lib.sandbox.contract.conftest import (
    DOCKER_TEST_IMAGE,
    skip_without_docker,
)
from tests.unit.harness.sandbox_broker.egress_stack import (
    GATEWAY_PORT,
    HELPER_IMAGE,
    OWNER,
    TARGET_PORT,
    StackOptions,
    sandbox_curl,
    scan_for_secret,
    start_stack,
    stop_stack,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator, Sequence

    from app.harness.sandbox.docker_broker import (
        DockerSandboxBroker,
        DockerSandboxHandle,
    )
    from harness_core.sandbox.types import RuntimeMode
    from tests.unit.harness.sandbox_broker.egress_stack import EgressStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600, method="thread")]

PUBLIC_NAME = "public.g21.test"
PUBLIC_ADDRESS = "93.184.216.34"
GIT = "git.g21.test"
UNLISTED = "unlisted.g21.test"
GATEWAY_ALIAS = "gw.g21.test"
DOCKER_HOST = "dockerhost.g21.test"
PROXY_USER = "svc-pipeshub"
PROXY_PASSWORD = "corp-" + uuid.uuid4().hex
LEASE = LeaseRef("worker-g21", 1)

_CONNECT = r"""
import socket, sys
s = socket.create_connection(("egress-proxy", 8097), timeout=10)
s.sendall(f"CONNECT {sys.argv[1]} HTTP/1.1\r\nHost: {sys.argv[1]}\r\n\r\n".encode())
head = b""
while b"\r\n\r\n" not in head:
    chunk = s.recv(4096)
    if not chunk:
        break
    head += chunk
lines = head.split(b"\r\n\r\n", 1)
status = lines[0].decode("latin-1").split("\r\n")[0].split(" ")
refusal = next((l.split(":", 1)[1].strip() for l in lines[0].decode("latin-1").split("\r\n")
                if l.lower().startswith("x-egress-refusal:")), "")
echo = ""
if len(status) > 1 and status[1] == "200":
    s.sendall(b"client-bytes")
    echo = s.recv(4096).decode("latin-1")
print(status[1] if len(status) > 1 else "none", refusal or "-", echo or "-")
"""


@pytest.fixture(scope="module")
def egress_stack() -> Iterator[EgressStack]:
    import docker

    skip_without_docker()
    client = docker.from_env()
    try:
        client.images.get(HELPER_IMAGE)
    except Exception:
        client.close()
        pytest.skip(f"image {HELPER_IMAGE!r} not present")

    def proxy_hosts(stack: EgressStack) -> dict[str, str]:
        target = stack.address(stack.target)
        return {
            PUBLIC_NAME: PUBLIC_ADDRESS,
            GIT: target,
            UNLISTED: target,
            GATEWAY_ALIAS: stack.address(stack.gateway),
            DOCKER_HOST: stack.rig_gateway,
        }

    def proxy_env(stack: EgressStack) -> dict[str, str]:
        # The internal Git server by name; and, listed on purpose, PipesHub's own: a gateway, the Docker host and the
        # proxy's own control port.
        entries = [
            f"{GIT}:{TARGET_PORT}",
            f"{GATEWAY_ALIAS}:{GATEWAY_PORT}",
            f"{DOCKER_HOST}:{GATEWAY_PORT}",
            f"{stack.proxy}:{EGRESS_CONTROL_PORT}",
        ]
        return {"EGRESS_PRIVATE_ALLOW": ",".join(entries)}

    stack = start_stack(
        client,
        StackOptions(
            tunnels=True,
            upstream_proxy=f"{PROXY_USER}:{PROXY_PASSWORD}",
            proxy_env=proxy_env,
            proxy_hosts=proxy_hosts,
        ),
    )
    yield stack
    stop_stack(stack)
    client.close()


def runner_spec(stack: EgressStack) -> MCPRunnerSpec:
    hosts = (PUBLIC_NAME, GIT, UNLISTED, GATEWAY_ALIAS, DOCKER_HOST, stack.proxy)
    return MCPRunnerSpec(
        server_id="srv-g21",
        image=DOCKER_TEST_IMAGE,
        command="true",
        egress=tuple(RunnerEgressHost(h, (80, 443, TARGET_PORT, EGRESS_CONTROL_PORT)) for h in hosts),
    )


class Box:
    def __init__(self, handle: DockerSandboxHandle, env: dict[str, str]) -> None:
        self.handle = handle
        self.env = env

    async def out(self, argv: Sequence[str], *, stdin: bytes | None = None) -> str:
        process = await self.handle.exec(argv, env=self.env, stdin=stdin is not None)
        if stdin is not None:
            await process.write(stdin)
            await process.close_stdin()
        out, err, _ = await run(process, timeout_s=60)
        return (out + err).decode("utf-8", "replace").strip()

    async def http(self, url: str) -> tuple[str, str]:
        """``(<status> <refusal>, body)`` of a plain-HTTP GET through the egress proxy."""
        status, _, body = (await self.out(sandbox_curl(url, {}))).partition("\n")
        return status.strip(), body


@pytest.fixture
async def box(
    egress_docker_broker: DockerSandboxBroker, egress_stack: EgressStack, runtime_mode: RuntimeMode
) -> AsyncIterator[Box]:
    spec = runner_spec(egress_stack)
    sandbox = SandboxSpec(
        image_digest=DOCKER_TEST_IMAGE,
        mode=runtime_mode,
        resources=Resources(cpus=0.5, memory_mb=256, pids=64, scratch_mb=64),
        egress=runner_egress_policy(spec),
        mounts=(MountSpec(WORK),),
        labels={**OWNER, "pipeshub.agent": "WP-34", "orgId": "org-g21", "runId": "run-g21", "serverId": spec.server_id},
    )
    handle = await egress_docker_broker.acquire(sandbox, lease=LEASE, idempotency_key=f"k-g21-{uuid.uuid4().hex[:6]}")
    yield Box(handle, runner_base_env(spec))
    await egress_docker_broker.release(handle)


def upstream_lines(stack: EgressStack) -> list[str]:
    return [line for line in stack.logs(stack.upstream_proxy or "").splitlines() if line.startswith("UPROXY ")]


async def test_sec_eg_13_public_http_is_chained_through_the_upstream_proxy_with_its_credential(
    box: Box, egress_stack: EgressStack
) -> None:
    before = len(upstream_lines(egress_stack))
    status, body = await box.http(f"http://{PUBLIC_NAME}/docs?q=1")
    assert status == "200", (status, body)
    # The corporate proxy was asked for the address the egress proxy checked, never the name, and accepted the credential.
    assert body.strip() == f"chained GET http://{PUBLIC_ADDRESS}/docs?q=1 HTTP/1.1"
    assert upstream_lines(egress_stack)[before:] == [f"UPROXY GET http://{PUBLIC_ADDRESS}/docs?q=1 HTTP/1.1 AUTH ok"]


async def test_sec_eg_13_tls_is_a_connect_through_the_upstream_proxy(box: Box, egress_stack: EgressStack) -> None:
    before = len(upstream_lines(egress_stack))
    answer = await box.out(["python3", "-c", _CONNECT, f"{PUBLIC_NAME}:443"])
    assert answer == "200 - tunnel-echo:client-bytes", answer
    assert upstream_lines(egress_stack)[before:] == [f"UPROXY CONNECT {PUBLIC_ADDRESS}:443 HTTP/1.1 AUTH ok"]


async def test_sec_eg_13_the_proxy_credential_never_reaches_the_sandbox_or_the_logs(
    box: Box, egress_stack: EgressStack
) -> None:
    _ = await box.http(f"http://{PUBLIC_NAME}/")
    assert (await box.out(scan_for_secret(), stdin=PROXY_PASSWORD.encode())).startswith("hits 0"), "sandbox holds it"
    assert PROXY_PASSWORD not in egress_stack.logs(egress_stack.proxy)
    assert all(PROXY_PASSWORD not in value for value in box.env.values())


async def test_sec_eg_13_an_explicit_private_host_port_is_reached_direct(box: Box, egress_stack: EgressStack) -> None:
    before = len(upstream_lines(egress_stack))
    status, body = await box.http(f"http://{GIT}:{TARGET_PORT}/")
    assert status == "200", (status, body)
    assert "Directory listing" in body
    assert upstream_lines(egress_stack)[before:] == [], "an internal destination never goes to the corporate proxy"


async def test_sec_eg_13_private_addresses_stay_denied_by_default(box: Box, egress_stack: EgressStack) -> None:
    before = egress_stack.logs(egress_stack.target)
    # The same server as the listed name, by a name no entry lists.
    assert (await box.http(f"http://{UNLISTED}:{TARGET_PORT}/"))[0] == "403 destination_private_address"
    # The listed host on a port it is not listed for is refused too.
    assert (await box.http(f"http://{GIT}:80/"))[0] == "403 destination_private_address"
    assert egress_stack.logs(egress_stack.target) == before


@pytest.mark.parametrize("which", ["gateway", "docker-host", "proxy-itself"])
async def test_sec_iso_09_pipeshub_internals_are_denied_even_when_listed(
    box: Box, egress_stack: EgressStack, which: str
) -> None:
    url = {
        "gateway": f"http://{GATEWAY_ALIAS}:{GATEWAY_PORT}/",  # a gateway's real address
        "docker-host": f"http://{DOCKER_HOST}:{GATEWAY_PORT}/",  # the Docker host (the rig gateway)
        "proxy-itself": f"http://{egress_stack.proxy}:{EGRESS_CONTROL_PORT}/healthz",  # its own control plane
    }[which]
    requests = egress_stack.gateway_requests()
    assert (await box.http(url))[0] == "403 destination_private_address"
    assert egress_stack.gateway_requests() == requests
