"""DT-1..DT-5: a runner's HTTPS through an opaque CONNECT tunnel on real Docker (P5.4b).

A sandbox from the Docker broker gets exactly an MCP runner's egress policy
(``runner_egress_policy``) and environment (``runner_base_env``: ``HTTPS_PROXY``),
and runs ``curl`` against an HTTPS server that holds a certificate from a CA the
test made. The proxy runs with tunnels on (``egress_entry``). The TLS server is on
the stack's private network, so it is listed with a trusted upstream, as P5.10's
MD-4 lists its plain-HTTP target; the refusals go through the proxy's own
resolver. runc lane on ``pxh-sandbox:x1``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.sandbox.mcp_runner.policy import runner_egress_policy
from harness_core.sandbox.mcp_runner.sandboxed import runner_base_env
from harness_core.sandbox.mcp_runner.types import MCPRunnerSpec, RunnerEgressHost
from harness_core.sandbox.types import LeaseRef, MountSpec, Resources, SandboxSpec
from harness_core.testing.sandbox.contract import WORK, run
from tests.support.tls_certs import make_pki
from tests.unit.agent_loop_lib.sandbox.contract.conftest import (
    DOCKER_TEST_IMAGE,
    skip_without_docker,
)
from tests.unit.harness.sandbox_broker.egress_stack import (
    HELPER_IMAGE,
    OWNER,
    TLS_PORT,
    StackOptions,
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

LISTED = "https-target.p54b.test"
REBIND = "rebind.p54b.test"
LITERAL = "93.184.216.34"
VENDOR = "ghp_" + "T" * 36
PKI = make_pki(LISTED)
LEASE = LeaseRef("worker-dt", 1)

# Raw CONNECT from inside the sandbox; prints the proxy's status line and its refusal header.
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
lines = head.decode("latin-1").split("\r\n")
refusal = next((l.split(":", 1)[1].strip() for l in lines if l.lower().startswith("x-egress-refusal:")), "")
print(lines[0].split(" ")[1] if lines and " " in lines[0] else "none", refusal)
"""


@pytest.fixture(scope="module")
def egress_stack() -> Iterator[EgressStack]:
    """The module's proxy with tunnels on, a TLS target, and a name the proxy resolves to a private address."""
    import docker

    skip_without_docker()
    client = docker.from_env()
    try:
        client.images.get(HELPER_IMAGE)
    except Exception:
        client.close()
        pytest.skip(f"image {HELPER_IMAGE!r} not present")

    def proxy_hosts(stack: EgressStack) -> dict[str, str]:
        return {REBIND: stack.address(stack.target)}

    stack = start_stack(
        client, StackOptions(tunnels=True, tls_pki=(PKI.cert_pem, PKI.key_pem), proxy_hosts=proxy_hosts)
    )
    yield stack
    stop_stack(stack)
    client.close()


def runner_spec(stack: EgressStack) -> MCPRunnerSpec:
    assert stack.tls is not None
    return MCPRunnerSpec(
        server_id="srv-tunnel",
        image=DOCKER_TEST_IMAGE,
        command="true",
        egress=(
            RunnerEgressHost(LISTED, (443,), upstream=f"https://{stack.tls}:{TLS_PORT}"),
            RunnerEgressHost(REBIND, (443,)),
            RunnerEgressHost(LITERAL, (443,)),
        ),
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

    async def connect(self, authority: str) -> tuple[str, str]:
        status, _, refusal = (await self.out(["python3", "-c", _CONNECT, authority])).partition(" ")
        return status, refusal.strip()


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
        labels={**OWNER, "pipeshub.agent": "P5.4b", "orgId": "org-dt", "runId": "run-dt", "serverId": spec.server_id},
    )
    handle = await egress_docker_broker.acquire(sandbox, lease=LEASE, idempotency_key="k-dt-1")
    yield Box(handle, runner_base_env(spec))
    await egress_docker_broker.release(handle)


async def test_dt1_dt5_a_listed_https_host_through_an_opaque_audited_tunnel(box: Box, egress_stack: EgressStack) -> None:
    assert box.env["HTTPS_PROXY"] == "http://egress-proxy:8097"
    _ = await box.out(["sh", "-c", f"cat > {WORK}/ca.pem"], stdin=PKI.ca_pem)
    answer = await box.out(
        ["curl", "-s", "-m", "20", "--cacert", f"{WORK}/ca.pem", "-H", f"X-Vendor: {VENDOR}", "-w", "\n%{http_code}",
         f"https://{LISTED}/v1/repos"]
    )
    # The certificate was verified for the listed name (no -k); the server saw our headers in clear: opaque.
    assert answer.endswith("200"), answer
    assert "GET /v1/repos HTTP/1.1" in answer and f"Host: {LISTED}" in answer and VENDOR in answer
    assert "TLS GET /v1/repos HTTP/1.1" in egress_stack.logs(egress_stack.tls or "")
    logs = egress_stack.logs(egress_stack.proxy)
    opened = [line for line in logs.splitlines() if "egress.tunnel_opened" in line]
    closed = [line for line in logs.splitlines() if "egress.tunnel_closed" in line]
    assert len(opened) == 1 and len(closed) == 1, logs[-2000:]
    assert f"host={LISTED}" in opened[0] and "runId=run-dt" in opened[0] and "serverId=srv-tunnel" in opened[0]
    assert VENDOR not in logs and "/v1/repos" not in logs


async def test_dt2_a_host_off_the_list_is_refused(box: Box) -> None:
    assert await box.connect("api.github.com:443") == ("403", "destination_not_allowed")


async def test_dt3_an_ip_literal_is_refused_even_when_listed(box: Box) -> None:
    assert await box.connect(f"{LITERAL}:443") == ("403", "tunnel_ip_literal")


async def test_dt4_a_listed_name_answering_a_private_address_is_refused(box: Box, egress_stack: EgressStack) -> None:
    before = egress_stack.logs(egress_stack.target)
    assert await box.connect(f"{REBIND}:443") == ("403", "destination_private_address")
    assert egress_stack.logs(egress_stack.target) == before
