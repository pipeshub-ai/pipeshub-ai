"""R31GVISO-FU-3: a turn its sandbox's memory limit ended fails the run as ``out_of_memory`` with the limit named.

Under gVisor the host's OOM killer sees one process, the Sentry, so the whole sandbox ends and the turn was read as
"sandbox is gone": a lost turn, released and driven again into the same limit. Under runc the kernel kills the
engine's own process, which read as a crashed engine. Either way the controller now asks the broker, before it
releases the sandbox, whether the memory limit ended it (the container's ``OOMKilled``), and the run fails once,
typed, with the limit; any other lost turn is released as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, override

import pytest

from harness_core.engines.errors import EngineCrashed
from harness_core.engines.outcome.faults import DEGRADE, fault_of
from harness_core.failpoints import Always, Arm, Raise
from harness_core.sandbox.errors import SandboxNotFound, SandboxOutOfMemory
from harness_core.sandbox.ports import memory_exhausted
from harness_core.testing import armed
from harness_core.testing.engines.durable import CountingBroker, EngineWorld
from harness_core.testing.ids import SeededIds

if TYPE_CHECKING:
    from harness_core.sandbox.types import LeaseRef, SandboxSpec
    from harness_core.testing.sandbox.fake import FakeSandboxHandle

RUN = "run-oom"
POINT = "engine.turn.after_materialize"


class _MemoryLimitedBroker(CountingBroker):
    """Every sandbox it makes is one whose memory limit ended a process, as the container's OOMKilled says."""

    limit_mb = 0

    @override
    async def acquire(self, spec: SandboxSpec, *, lease: LeaseRef, idempotency_key: str) -> FakeSandboxHandle:
        handle = await super().acquire(spec, lease=lease, idempotency_key=idempotency_key)
        handle.state.out_of_memory = True
        self.limit_mb = spec.resources.memory_mb
        return handle


def _world(*, out_of_memory: bool) -> EngineWorld:
    world = EngineWorld()
    if out_of_memory:
        world.broker = _MemoryLimitedBroker(
            clock=world.clock, ids=SeededIds(), modes=world.broker.modes(), egress_proxy=world.proxy
        )
    world.admit(RUN, "say hello")
    return world


@pytest.mark.parametrize("lost", [EngineCrashed, SandboxNotFound], ids=["engine_killed_runc", "sandbox_gone_gvisor"])
async def test_oom_1_a_turn_the_memory_limit_ended_fails_once_with_the_limit(lost: type[Exception]) -> None:
    world = _world(out_of_memory=True)
    with armed({POINT: Arm(Raise(lost, "ended under the turn"), Always())}):
        disposition = await world.attempt(RUN)
    assert (disposition.status, disposition.reason) == ("failed", "out_of_memory")
    broker = world.broker
    assert isinstance(broker, _MemoryLimitedBroker)
    [failed] = [e.data for e in world.events(RUN) if e.type == "run.failed"]
    assert failed == {"errorType": "out_of_memory", "memoryLimitMb": broker.limit_mb}
    assert broker.acquisitions == 1, "never driven again into the same limit"
    assert world.journal.status(RUN) == "failed"


async def test_oom_2_a_lost_turn_without_an_oom_is_released_and_driven_again_as_before() -> None:
    world = _world(out_of_memory=False)
    with armed({POINT: Arm(Raise(EngineCrashed, "ended under the turn"), Always())}):
        disposition = await world.attempt(RUN)
    assert disposition.status == "released"
    assert not [e for e in world.events(RUN) if e.type == "run.failed"]
    assert world.journal.status(RUN) == "queued"


def test_oom_3_the_fault_table_fails_an_out_of_memory_end_and_names_its_code() -> None:
    fault = fault_of(SandboxOutOfMemory("limit", memory_mb=4096))
    assert fault is not None
    assert (fault.kind, fault.code, fault.degrade) == ("out_of_memory", "out_of_memory", "fail")
    assert DEGRADE["out_of_memory"] == "fail"


async def test_oom_4_a_handle_that_cannot_tell_reads_as_no_oom() -> None:
    class Silent:
        pass

    class Failing:
        async def memory_exhausted(self) -> bool:
            raise RuntimeError("broker away")

    assert await memory_exhausted(Silent()) is False
    assert await memory_exhausted(Failing()) is False
