"""Tests that leak on purpose, run only by ``test_sbxleak_docker.py`` in a pytest process of their own.

Each acquires a sandbox with egress (its own network, its volumes) from a Docker broker that no fixture tears down,
writes what it made to ``SBXLEAK_READY``, then fails, or hangs until a timeout or a signal ends the process. Whatever
is left afterwards outlived its test (SBXLEAK). The file name keeps pytest from collecting it in a normal run.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from app.harness.sandbox.docker_broker import DockerSandboxBroker
from app.harness.sandbox.docker_client import DockerClientProvider
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
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox.contract import WORK
from tests.unit.agent_loop_lib.sandbox.contract.conftest import DOCKER_TEST_IMAGE
from tests.unit.harness.sandbox_broker.conftest import egress_wiring
from tests.unit.harness.sandbox_broker.egress_stack import OWNER

if TYPE_CHECKING:
    from harness_core.sandbox.egress.client import RemoteEgressBindings
    from tests.unit.harness.sandbox_broker.egress_stack import EgressStack

pytestmark = [pytest.mark.docker]


async def _acquire(stack: EgressStack, bindings: RemoteEgressBindings) -> None:
    namespace = os.environ["SBXLEAK_NAMESPACE"]
    prefix = "pxh-sbxleak-" + namespace.rsplit(".", 1)[1]
    provider = DockerClientProvider(max_workers=4)
    clock = ManualClock()
    broker = await DockerSandboxBroker.create(
        provider=provider,
        clock=clock,
        ids=RandomIds(),
        allowed_images=frozenset({DOCKER_TEST_IMAGE}),
        label_namespace=namespace,
        volume_prefix=prefix,
        egress=egress_wiring(provider, clock, stack, bindings, prefix),
    )
    spec = SandboxSpec(
        image_digest=DOCKER_TEST_IMAGE,
        mode=RuntimeMode.DOCKER_HARDENED,
        resources=Resources(cpus=0.5, memory_mb=128, pids=64, scratch_mb=16),
        egress=EgressPolicy(destinations=(EgressDestination("model-gateway", (EGRESS_PROXY_PORT,)),)),
        mounts=(MountSpec(WORK),),
        labels={**OWNER, "pipeshub.agent": "SBXLEAK", "runId": "run-sbxleak"},
    )
    handle = await broker.acquire(spec, lease=LeaseRef("worker-sbxleak", 1), idempotency_key="k-sbxleak")
    Path(os.environ["SBXLEAK_READY"]).write_text(
        json.dumps({"sandbox": handle.id, "stack": stack.suffix, "pid": os.getpid()})
    )


@pytest.mark.timeout(180)
async def test_acquires_then_fails(egress_stack: EgressStack, egress_bindings: RemoteEgressBindings) -> None:
    await _acquire(egress_stack, egress_bindings)
    pytest.fail("fails mid-run with its sandbox still held")


@pytest.mark.timeout(5, method="thread", func_only=True)
async def test_acquires_then_hangs_past_a_thread_timeout(
    egress_stack: EgressStack, egress_bindings: RemoteEgressBindings
) -> None:
    await _acquire(egress_stack, egress_bindings)
    time.sleep(600)


@pytest.mark.timeout(5, method="signal", func_only=True)
async def test_acquires_then_hangs_where_the_signal_timeout_cannot_end_it(
    egress_stack: EgressStack, egress_bindings: RemoteEgressBindings
) -> None:
    await _acquire(egress_stack, egress_bindings)
    # A native wait the SIGALRM failure cannot interrupt for long: the hang guard's ``os._exit`` ends it.
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        try:
            time.sleep(600)
        except BaseException:
            continue


@pytest.mark.timeout(900)
async def test_acquires_then_waits_for_a_signal(egress_stack: EgressStack, egress_bindings: RemoteEgressBindings) -> None:
    await _acquire(egress_stack, egress_bindings)
    time.sleep(600)
