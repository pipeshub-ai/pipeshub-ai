"""SF-D1, SF-D2 (SIGNFIX) on Docker: a kept sign-in sandbox reaches its tool gateway through the real egress proxy
container, for each attempt it serves, with that attempt's token.

In a manual test, every tool call of a signed-in Claude Code was refused ``token_unavailable``, because a kept
sandbox's binding named no run attempt. The stack's proxy here issues tokens only for the attempt a binding names, as
Node does; the real ``DockerSandboxBroker`` re-binds the kept sandbox for the next message over the control plane, as
in that test. runc and gVisor lanes on the sandbox test image.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import pytest

from harness_core.engines.attempt_labels import bound_labels
from harness_core.engines.controller import KEPT_LABEL
from harness_core.sandbox.egress.types import EGRESS_PROXY_PORT
from harness_core.sandbox.types import (
    EgressDestination,
    EgressPolicy,
    LeaseRef,
    MountSpec,
    Resources,
    SandboxSpec,
    TokenRoute,
)
from harness_core.testing.sandbox.contract import WORK, parse_http, placeholder, run
from tests.unit.agent_loop_lib.sandbox.contract.conftest import (
    DOCKER_TEST_IMAGE,
    skip_without_docker,
)
from tests.unit.harness.sandbox_broker.egress_stack import (
    HELPER_IMAGE,
    OWNER,
    StackOptions,
    sandbox_curl,
    start_stack,
    stop_stack,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from app.harness.sandbox.docker_broker import (
        DockerSandboxBroker,
        DockerSandboxHandle,
    )
    from harness_core.sandbox.types import RuntimeMode
    from tests.unit.harness.sandbox_broker.egress_stack import EgressStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600, method="thread")]

TOOLS = f"http://tool-gateway:{EGRESS_PROXY_PORT}/mcp"
KEPT = {**OWNER, "pipeshub.agent": "SIGNFIX", "orgId": "o-sf", "sessionId": "s-sf", KEPT_LABEL: "1"}


@pytest.fixture(scope="module")
def egress_stack() -> Iterator[EgressStack]:
    import docker

    skip_without_docker()
    client = docker.from_env()
    try:
        client.images.get(HELPER_IMAGE)
    except Exception:
        client.close()
        pytest.skip("helper image not present")
    stack = start_stack(client, StackOptions(attempt_tokens=True))
    yield stack
    stop_stack(stack)
    client.close()


def _spec(ph: str, mode: RuntimeMode, labels: Mapping[str, str]) -> SandboxSpec:
    return SandboxSpec(
        image_digest=DOCKER_TEST_IMAGE,
        mode=mode,
        resources=Resources(cpus=0.5, memory_mb=256, pids=64, scratch_mb=64),
        egress=EgressPolicy(
            destinations=(EgressDestination("tool-gateway", (EGRESS_PROXY_PORT,)),),
            token_routes=(TokenRoute(ph, "tool-gateway", "gateway:tools"),),
        ),
        mounts=(MountSpec(WORK),),
        labels=labels,
    )


async def _tools(handle: DockerSandboxHandle, ph: str, stack: EgressStack) -> tuple[int, str, str]:
    """``tools/list`` from inside the sandbox: status, the proxy's refusal (or ``-``), and the authorization the
    gateway received (from its own log: the proxy scrubs the token out of the answer the sandbox sees)."""
    seen = stack.gateway_requests()
    request = sandbox_curl(
        TOOLS, {"Authorization": f"Bearer {ph}", "Content-Type": "application/json"}, via_proxy=False,
        method="POST", data='{"jsonrpc":"2.0","id":1,"method":"tools/list"}',
    )
    out, _, _ = await run(await handle.exec(request, env={}), timeout_s=60)
    status, refusal, _ = parse_http(out)
    if status != 200:
        return status, refusal, ""
    deadline = time.monotonic() + 10
    # The daemon copies the gateway's output to its log asynchronously: polling it is the only signal.
    while stack.gateway_requests() <= seen and time.monotonic() < deadline:  # noqa: ASYNC110
        await asyncio.sleep(0.2)
    received = stack.gateway_authorizations()[seen:]
    assert len(received) == 1, received
    assert ph not in received[0], "the gateway got the placeholder, not a token"
    return status, refusal, received[0]


async def test_sfd1_each_message_in_the_kept_sandbox_reaches_its_tools_with_its_own_attempts_token(
    egress_docker_broker: DockerSandboxBroker, runtime_mode: RuntimeMode, egress_stack: EgressStack
) -> None:
    first_ph, second_ph = placeholder(), placeholder()
    key = f"signin:{first_ph}"
    first = await egress_docker_broker.acquire(
        _spec(first_ph, runtime_mode, {**KEPT, **bound_labels("run-1", "run-1.a1")}), lease=LeaseRef("w-1", 1),
        idempotency_key=key,
    )
    try:
        status, refusal, first_token = await _tools(first, first_ph, egress_stack)
        assert (status, refusal) == (200, "-")
        await egress_docker_broker.release(first, keep_warm_s=600)
        # The next message: the same kept sandbox, re-acquired and bound again for the next run's attempt.
        second = await egress_docker_broker.acquire(
            _spec(second_ph, runtime_mode, {**KEPT, **bound_labels("run-2", "run-2.a1")}), lease=LeaseRef("w-2", 2),
            idempotency_key=key,
        )
        assert second.id == first.id
        status, refusal, second_token = await _tools(second, second_ph, egress_stack)
        assert (status, refusal) == (200, "-")
        assert second_token != first_token, "the next message must not carry the last run's token"
        # The last message's placeholder no longer opens anything.
        assert (await _tools(second, first_ph, egress_stack))[0] != 200
    finally:
        await egress_docker_broker.release(first)


async def test_sfd2_a_kept_sandbox_naming_no_attempt_is_refused(
    egress_docker_broker: DockerSandboxBroker, runtime_mode: RuntimeMode, egress_stack: EgressStack
) -> None:
    ph = placeholder()
    handle = await egress_docker_broker.acquire(
        _spec(ph, runtime_mode, KEPT), lease=LeaseRef("w-1", 1), idempotency_key=f"signin:{ph}"
    )
    try:
        assert await _tools(handle, ph, egress_stack) == (503, "token_unavailable", "")
    finally:
        await egress_docker_broker.release(handle)
