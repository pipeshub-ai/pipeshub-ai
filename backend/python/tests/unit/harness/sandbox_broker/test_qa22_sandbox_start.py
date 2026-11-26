"""QA22-11 (SLO #17, tier 1): sandbox start on the Docker broker (runc, hardened), acquire to a finished command.

One start is what a run's first ``run_code`` waits for: ``acquire`` (container, its scratch volume and workspace
mount), then the first command in it run to its exit. Cold: a broker without a pool. Warm: the broker's pool primed
with the run's shape and refilled between starts, as an idle host's background refill would (the refill is not
timed). ``QA22_SANDBOX_SAMPLES`` starts per lane (20 by default); ``QA22_SANDBOX_REPORT`` appends each lane's
``ready_ms`` and the load average at each start as a JSON line, which ``bench_harness.py --profile sandbox`` gates.
Tier 2 (gVisor) needs ``runsc``, which this lane skips (QA-25).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

import pytest

from app.harness.sandbox.docker_broker import DockerSandboxBroker
from app.harness.sandbox.docker_client import DockerClientProvider
from harness_core.ids import RandomIds
from harness_core.sandbox.pool import WarmPoolConfig
from harness_core.sandbox.types import (
    LeaseRef,
    MountSpec,
    Resources,
    RuntimeMode,
    SandboxSpec,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox.contract import WORK
from tests.unit.agent_loop_lib.sandbox.contract.conftest import (
    DOCKER_TEST_IMAGE,
    skip_without_docker,
)
from tests.unit.harness.sandbox_broker.conftest import cleanup

pytestmark = [pytest.mark.docker, pytest.mark.slow, pytest.mark.timeout(1800, method="thread")]

SAMPLES = int(os.environ.get("QA22_SANDBOX_SAMPLES", "20"))
POOL_CEILING = 2
OWNER = {"pipeshub.harness.owner": "x-lead", "pipeshub.agent": "QA-22"}
LEASE = LeaseRef("worker-qa22", 1)


def _spec() -> SandboxSpec:
    return SandboxSpec(
        image_digest=DOCKER_TEST_IMAGE,
        mode=RuntimeMode.DOCKER_HARDENED,
        resources=Resources(cpus=0.5, memory_mb=256, pids=64, scratch_mb=64),
        mounts=(MountSpec(WORK),),
        labels=dict(OWNER),
    )


async def _starts(*, warm: bool) -> tuple[list[float], list[bool], list[float]]:
    skip_without_docker()
    namespace = f"io.pipeshub.qa22.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-qa22-" + namespace.rsplit(".", 1)[1]
    provider = DockerClientProvider(max_workers=16)
    pool = WarmPoolConfig(ceiling=POOL_CEILING, learn_images=frozenset({DOCKER_TEST_IMAGE}), labels=OWNER) if warm else None
    broker = await DockerSandboxBroker.create(
        provider=provider, clock=ManualClock(), ids=RandomIds(), allowed_images=frozenset({DOCKER_TEST_IMAGE}),
        label_namespace=namespace, volume_prefix=prefix, warm_pool=pool,
    )
    ready: list[float] = []
    served_warm: list[bool] = []
    loads: list[float] = []
    try:
        if warm:
            broker.pool.learn(_spec())
            assert await broker.fill_warm() == POOL_CEILING
        for _ in range(SAMPLES):
            warm_before = broker.warm_ids()
            loads.append(os.getloadavg()[0])
            started = time.perf_counter()
            handle = await broker.acquire(_spec(), lease=LEASE, idempotency_key=uuid.uuid4().hex)
            process = await handle.exec(["true"], env={})
            code = await process.wait(timeout_s=60)
            ready.append((time.perf_counter() - started) * 1000)
            assert code == 0
            served_warm.append(handle.id in warm_before)
            await broker.release(handle)
            if warm:
                _ = await broker.fill_warm()
    finally:
        await broker.aclose()
        await provider.run_blocking(cleanup, provider, namespace, prefix)
        provider.close()
    return ready, served_warm, loads


def _report(lane: str, ready: list[float], loads: list[float]) -> None:
    path = os.environ.get("QA22_SANDBOX_REPORT")
    if path:
        with Path(path).open("a", encoding="utf-8") as out:
            _ = out.write(json.dumps({"lane": lane, "ready_ms": [round(v, 1) for v in ready], "load1": loads}) + "\n")


async def test_qa22_11_cold_start_runs_a_command_in_a_new_sandbox() -> None:
    ready, served_warm, loads = await _starts(warm=False)
    _report("cold", ready, loads)
    assert len(ready) == SAMPLES
    assert not any(served_warm)


async def test_qa22_11_warm_start_is_served_from_the_pool() -> None:
    ready, served_warm, loads = await _starts(warm=True)
    _report("warm", ready, loads)
    assert len(ready) == SAMPLES
    assert all(served_warm), "a start was not served from the pool"
