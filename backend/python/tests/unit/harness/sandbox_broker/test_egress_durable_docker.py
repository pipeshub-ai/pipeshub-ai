"""DD-1, DD-2: a killed and restarted egress proxy container keeps its sandboxes, and refuses what it cannot verify (P5.4b).

The proxy keeps its state in a Redis container of the stack's own, through the
production ``RedisEgressStateStore`` (``egress_entry``). runc lane on ``pxh-sandbox:x1``.
"""

from __future__ import annotations

import time
import urllib.request
from typing import TYPE_CHECKING

import pytest

from harness_core.sandbox.egress.tokens import InMemoryTokenSource
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
    from collections.abc import Iterator

    from app.harness.sandbox.docker_broker import (
        DockerSandboxBroker,
        DockerSandboxHandle,
    )
    from harness_core.sandbox.egress.client import RemoteEgressBindings
    from harness_core.sandbox.types import RuntimeMode
    from tests.unit.harness.sandbox_broker.egress_stack import EgressStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600, method="thread")]

MODEL = f"http://model-gateway:{EGRESS_PROXY_PORT}/v1/ping"
LEASE = LeaseRef("worker-dd", 1)
STATE_NAMESPACE = "pxh-p54b:harness:egress:{default}:"
# Well formed, but not sealed under the proxy's key (someone who can write to Redis).
FORGED = "v1.Zm9yZ2VkLWJ5LXNvbWVvbmUtd2l0aC1yZWRpcy1hY2Nlc3MtMDEyMw=="


@pytest.fixture(scope="module")
def egress_stack() -> Iterator[EgressStack]:
    import docker

    skip_without_docker()
    client = docker.from_env()
    try:
        client.images.get(HELPER_IMAGE)
        client.images.get("redis:7.4-bookworm")
    except Exception:
        client.close()
        pytest.skip("helper or redis image not present")
    stack = start_stack(client, StackOptions(redis_state=True))
    yield stack
    stop_stack(stack)
    client.close()


def _spec(ph: str, mode: RuntimeMode, run_id: str) -> SandboxSpec:
    return SandboxSpec(
        image_digest=DOCKER_TEST_IMAGE,
        mode=mode,
        resources=Resources(cpus=0.5, memory_mb=256, pids=64, scratch_mb=64),
        egress=EgressPolicy(
            destinations=(EgressDestination("model-gateway", (EGRESS_PROXY_PORT,)),),
            token_routes=(TokenRoute(ph, "model-gateway", "gateway:model"),),
        ),
        mounts=(MountSpec(WORK),),
        labels={**OWNER, "pipeshub.agent": "P5.4b", "runId": run_id},
    )


async def _http(handle: DockerSandboxHandle, ph: str) -> tuple[int, str]:
    process = await handle.exec(sandbox_curl(MODEL, {"Authorization": f"Bearer {ph}"}, via_proxy=False), env={})
    out, _, _ = await run(process, timeout_s=60)
    return parse_http(out)[:2]


def _kill_and_start(stack: EgressStack, *, between: object = None) -> None:
    stack.client.api.kill(stack.proxy)
    if callable(between):
        between()
    stack.client.api.start(stack.proxy)
    deadline = time.monotonic() + 60
    while True:
        try:
            with urllib.request.urlopen(stack.control_url + "/healthz", timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        if time.monotonic() > deadline:
            raise TimeoutError("the proxy did not come back")
        time.sleep(0.3)


def _redis(stack: EgressStack, *argv: str) -> str:
    assert stack.redis is not None
    result = stack.client.containers.get(stack.redis).exec_run(["redis-cli", *argv])
    return result.output.decode().strip()


async def test_dd1_a_killed_proxy_comes_back_with_its_sandboxes_and_their_tokens(
    egress_docker_broker: DockerSandboxBroker,
    egress_stack: EgressStack,
    egress_bindings: RemoteEgressBindings,
    runtime_mode: RuntimeMode,
) -> None:
    ph = placeholder()
    handle = await egress_docker_broker.acquire(_spec(ph, runtime_mode, "run-dd1"), lease=LEASE, idempotency_key=f"k-{ph}")
    try:
        token = InMemoryTokenSource.derive(egress_stack.secret, handle.id, "gateway:model", 0)
        assert await _http(handle, ph) == (200, "-")
        assert await egress_stack.wait_for_authorization(f"Bearer {token}")
        before = (await egress_bindings.health()).instance
        stored = _redis(egress_stack, "--scan", "--pattern", STATE_NAMESPACE + "*")
        assert f"binding/{handle.id}" in stored and token not in stored and ph not in stored

        _kill_and_start(egress_stack)

        # No broker resync: the restarted proxy restored the binding and the grant from Redis.
        assert (await egress_bindings.health()).instance == before
        requests = egress_stack.gateway_requests()
        assert await _http(handle, ph) == (200, "-")
        assert await egress_stack.wait_for_authorization(f"Bearer {token}")
        assert egress_stack.gateway_requests() == requests + 1
        assert egress_stack.gateway_authorizations()[-1] == f"Bearer {token}", "the stored grant, not a new token"
    finally:
        await egress_docker_broker.release(handle)


async def test_dd2_a_binding_it_cannot_verify_is_refused_until_its_broker_registers_it_again(
    egress_docker_broker: DockerSandboxBroker,
    egress_stack: EgressStack,
    egress_bindings: RemoteEgressBindings,
    runtime_mode: RuntimeMode,
) -> None:
    ph = placeholder()
    handle = await egress_docker_broker.acquire(_spec(ph, runtime_mode, "run-dd2"), lease=LEASE, idempotency_key=f"k-{ph}")
    try:
        assert await _http(handle, ph) == (200, "-")
        _ = await egress_docker_broker.resync_egress()
        before = (await egress_bindings.health()).instance
        key = f"{STATE_NAMESPACE}binding/{handle.id}"

        def forge() -> None:
            assert _redis(egress_stack, "SET", key, FORGED, "KEEPTTL") == "OK"

        _kill_and_start(egress_stack, between=forge)

        assert await _http(handle, ph) == (403, "unbound_source")
        assert (await egress_bindings.health()).instance != before
        logs = egress_stack.logs(egress_stack.proxy)
        assert "egress.state_unverified" in logs and "reason=seal_mismatch" in logs
        assert await egress_docker_broker.resync_egress()
        assert await _http(handle, ph) == (200, "-")
    finally:
        await egress_docker_broker.release(handle)
