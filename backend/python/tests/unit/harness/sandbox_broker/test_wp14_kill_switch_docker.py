"""J-19 / SEC-CR-03 / C6.14 on the real Docker broker: an egress proxy started with the kill switch on.

The operator's incident stop (``AGENT_HARNESS_KILL_SWITCH``) set on the proxy ends the sandbox side of every harness
run: no sandbox serving a run attempt is bound, so it cannot be created with an allowlist, while the standard chat's
code sandbox (no run attempt, where the stop sends new chats) still is, and still reaches its gateway.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.sandbox.egress.bindings import KILL_SWITCH
from harness_core.sandbox.egress.types import EGRESS_PROXY_PORT
from harness_core.sandbox.errors import SandboxRefusal
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

    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from harness_core.sandbox.egress.client import RemoteEgressBindings
    from harness_core.sandbox.types import RuntimeMode
    from tests.unit.harness.sandbox_broker.egress_stack import EgressStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(300, method="thread")]

LEASE = LeaseRef("worker-kill", 1)
MODEL = f"http://model-gateway:{EGRESS_PROXY_PORT}"


@pytest.fixture(scope="module")
def egress_stack() -> Iterator[EgressStack]:
    """This module's own proxy, started with the kill switch on (the conftest's is started without it)."""
    import docker

    skip_without_docker()
    client = docker.from_env()
    try:
        client.images.get(HELPER_IMAGE)
    except Exception:
        client.close()
        pytest.skip(f"image {HELPER_IMAGE!r} not present")
    stack = start_stack(client, StackOptions(kill_switch=True))
    yield stack
    stop_stack(stack)
    client.close()


def spec(mode: RuntimeMode, labels: Mapping[str, str]) -> tuple[SandboxSpec, str]:
    model_ph = placeholder()
    return (
        SandboxSpec(
            image_digest=DOCKER_TEST_IMAGE,
            mode=mode,
            resources=Resources(cpus=0.5, memory_mb=256, pids=64, scratch_mb=64),
            egress=EgressPolicy(
                destinations=(EgressDestination("model-gateway", (EGRESS_PROXY_PORT,)),),
                token_routes=(TokenRoute(model_ph, "model-gateway", "gateway:model"),),
            ),
            mounts=(MountSpec(WORK),),
            labels={**OWNER, **labels},
        ),
        model_ph,
    )


async def test_j_19_with_the_kill_switch_on_no_run_sandbox_gets_egress_but_the_standard_chat_s_does(
    egress_docker_broker: DockerSandboxBroker,
    runtime_mode: RuntimeMode,
    egress_stack: EgressStack,
    egress_bindings: RemoteEgressBindings,
) -> None:
    broker = egress_docker_broker
    assert (await egress_bindings.health()).kill_switch is True
    for labels in (
        {"orgId": "org-k", "sessionId": "ses-k", "runId": "run-k", "attemptId": "run-k-a1"},
        {"orgId": "org-k", "sessionId": "ses-k", "pipeshub.bound.runId": "run-k", "pipeshub.bound.attemptId": "run-k-a2"},
    ):
        refused_spec, model_ph = spec(runtime_mode, labels)
        with pytest.raises(SandboxRefusal) as caught:
            _ = await broker.acquire(refused_spec, lease=LEASE, idempotency_key=f"k-{model_ph}")
        assert caught.value.reason == KILL_SWITCH
    assert await egress_bindings.bound() == frozenset()

    legacy_spec, model_ph = spec(runtime_mode, {"legacyExecutor": "run"})
    handle = await broker.acquire(legacy_spec, lease=LEASE, idempotency_key=f"k-{model_ph}")
    try:
        before = egress_stack.gateway_requests()
        process = await handle.exec(sandbox_curl(f"{MODEL}/v1/ok", {"Authorization": f"Bearer {model_ph}"}), env={})
        out, _, _ = await run(process, timeout_s=60)
        assert parse_http(out)[0] == 200
        assert egress_stack.gateway_requests() == before + 1
    finally:
        await broker.release(handle)
