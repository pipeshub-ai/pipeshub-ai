"""SG-1..SG-3: the mount strategy follows the broker's capabilities."""

from __future__ import annotations

import pytest

from harness_core.sandbox.pool import WARM_POOL, WarmPoolConfig
from harness_core.sandbox.types import (
    PERSISTENT_MOUNTS,
    BrokerHealth,
    LeaseRef,
    MountSpec,
    RuntimeMode,
    SandboxSpec,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox import FakeSandboxBroker
from harness_core.workspace.sync import (
    TAR,
    VOLUME,
    SyncStrategies,
    SyncStrategy,
    broker_capabilities,
)


def _health(**detail: str) -> BrokerHealth:
    return BrokerHealth(ok=True, modes=frozenset({RuntimeMode.DOCKER_HARDENED}), detail=detail)


@pytest.mark.parametrize(
    ("detail", "strategy"),
    [
        ({PERSISTENT_MOUNTS: "true"}, VOLUME),
        ({}, TAR),
        ({PERSISTENT_MOUNTS: "false"}, TAR),
        ({"subpath": "true", "somethingNew": "true"}, TAR),
        ({PERSISTENT_MOUNTS: "true", "somethingNew": "true"}, VOLUME),
        # ST-1 (P5.9a): a broker with a warm pool serves scratch mounts only.
        ({PERSISTENT_MOUNTS: "true", WARM_POOL: "true"}, TAR),
        ({PERSISTENT_MOUNTS: "true", WARM_POOL: "false"}, VOLUME),
    ],
)
def test_sg1_the_registry_picks_from_capabilities(detail: dict[str, str], strategy: SyncStrategy) -> None:
    assert SyncStrategies().for_broker(_health(**detail)) is strategy


def test_sg1_a_registry_always_has_a_strategy_for_any_broker() -> None:
    with pytest.raises(ValueError, match="need nothing"):
        _ = SyncStrategies((VOLUME,))
    assert SyncStrategies((TAR, VOLUME)).select({PERSISTENT_MOUNTS}) is TAR
    assert broker_capabilities(_health(a="true", b="no")) == frozenset({"a"})


def test_sg2_mounts() -> None:
    assert TAR.mount("/workspace") == MountSpec("/workspace")
    assert TAR.mount("/workspace", source="org/s/ws") == MountSpec("/workspace")
    assert VOLUME.mount("/workspace", source="org/s/ws") == MountSpec("/workspace", source="org/s/ws")
    for unsafe in (None, "", "/abs", "a/../b", "a//b"):
        with pytest.raises(ValueError, match="source key"):
            _ = VOLUME.mount("/workspace", source=unsafe)


async def test_sg3_the_fake_reports_and_keeps_persistent_mounts() -> None:
    plain, persistent = FakeSandboxBroker(clock=ManualClock()), FakeSandboxBroker(clock=ManualClock(), persistent_mounts=True)
    assert PERSISTENT_MOUNTS not in (await plain.health()).detail
    assert (await persistent.health()).detail[PERSISTENT_MOUNTS] == "true"
    spec = SandboxSpec("fake@sha256:" + "0" * 64, RuntimeMode.DOCKER_HARDENED, mounts=(MountSpec("/ws", source="t/s/ws"),))
    first = await persistent.acquire(spec, lease=LeaseRef("w", 1), idempotency_key="one")
    first.put_file("/ws/kept.txt", b"kept")
    first.put_file("/ws/deleted.txt", b"gone soon")
    await persistent.release(first)
    second = await persistent.acquire(spec, lease=LeaseRef("w", 1), idempotency_key="two")
    assert dict(second.files) == {"/ws/kept.txt": b"kept", "/ws/deleted.txt": b"gone soon"}
    del second.files["/ws/deleted.txt"]
    _ = await persistent.reap(labels={}, older_than_s=0)
    third = await persistent.acquire(spec, lease=LeaseRef("w", 1), idempotency_key="three")
    assert dict(third.files) == {"/ws/kept.txt": b"kept"}
    await persistent.release(third)


async def test_st1_a_pooled_broker_with_persistent_mounts_gets_tar() -> None:
    pooled = FakeSandboxBroker(clock=ManualClock(), persistent_mounts=True, warm_pool=WarmPoolConfig(ceiling=1))
    assert SyncStrategies().for_broker(await pooled.health()) is TAR
    unpooled = FakeSandboxBroker(clock=ManualClock(), persistent_mounts=True)
    assert SyncStrategies().for_broker(await unpooled.health()) is VOLUME
