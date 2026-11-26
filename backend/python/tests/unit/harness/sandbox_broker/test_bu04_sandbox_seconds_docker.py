"""BU-04 (C9.07) on the Docker broker (runc): a lease is metered from the acquire handing the container to the run to
the release that ends it.

The container's own create and removal (seconds on a busy daemon) belong to the broker, not the run. The meter reads
the test's clock, which moves only while the run holds the sandbox and records whether the broker held the container
at each reading: both readings fall after its create and before its removal, and the interval is exactly the hold.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.harness.sandbox.docker_broker import DockerSandboxBroker
from harness_core.ids import RandomIds
from harness_core.sandbox.metering import RecordingBrokerMetrics
from harness_core.sandbox.types import LeaseRef, RuntimeMode
from tests.unit.agent_loop_lib.sandbox.contract.conftest import DOCKER_TEST_IMAGE

if TYPE_CHECKING:
    from app.harness.sandbox.docker_client import DockerClientProvider
    from harness_core.testing.clock import ManualClock
    from tests.unit.harness.sandbox_broker.conftest import DockerContractEnv

pytestmark = [pytest.mark.docker, pytest.mark.timeout(300, method="thread")]

HOLD_S = 5.0


class _HoldClock:
    """The meter's monotonic clock: each reading notes the sandboxes the broker held at that moment."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.broker: DockerSandboxBroker | None = None
        self.readings: list[tuple[float, frozenset[str]]] = []

    def __call__(self) -> float:
        assert self.broker is not None
        self.readings.append((self.now, frozenset(self.broker._sandboxes)))  # pyright: ignore[reportPrivateUsage]
        return self.now


@pytest.mark.parametrize("runtime_mode", [RuntimeMode.DOCKER_HARDENED], ids=["runc"])
async def test_bu04_d1_a_docker_lease_is_metered_from_its_handover_to_its_release(
    provider: DockerClientProvider,
    clock: ManualClock,
    namespace: str,
    volume_prefix: str,
    cleanup_namespace: None,
    sandbox_env: DockerContractEnv,
) -> None:
    metrics, hold = RecordingBrokerMetrics(), _HoldClock()
    broker = await DockerSandboxBroker.create(
        provider=provider,
        clock=clock,
        ids=RandomIds(),
        allowed_images=frozenset({DOCKER_TEST_IMAGE}),
        label_namespace=namespace,
        volume_prefix=volume_prefix,
        metrics=metrics,
        monotonic=hold,
    )
    hold.broker = broker
    try:
        spec = sandbox_env.spec(labels={"orgId": "org-bu04", "runId": "run-bu04"})
        handle = await broker.acquire(spec, lease=LeaseRef("worker-bu04", 1), idempotency_key="bu04-docker")
        hold.now += HOLD_S
        await broker.release(handle)
    finally:
        await broker.aclose()
    (interval,) = metrics.intervals
    assert (interval.phase, interval.sandbox_id, interval.mode) == ("active", handle.id, RuntimeMode.DOCKER_HARDENED.value)
    assert dict(interval.labels) == {"orgId": "org-bu04", "runId": "run-bu04"}
    assert interval.seconds == HOLD_S
    assert hold.readings == [(1000.0, frozenset({handle.id})), (1000.0 + HOLD_S, frozenset({handle.id}))]
