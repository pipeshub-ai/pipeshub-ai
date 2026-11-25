"""A code run holding a governor slot can still find the broker full (engine sandboxes count there too); it waits
out the rest of SANDBOX_SLOT_WAIT_S with the queued note, then gets the queue-style refusal."""

from __future__ import annotations

import time

import pytest

from app.agent_loop_lib.sandbox.capacity_wait import (
    CapacityWait,
    capacity_wait,
    retry_while_full,
)
from harness_core.sandbox.errors import BrokerUnavailable, SandboxError


def _full(exc: BaseException) -> bool:
    return isinstance(exc, BrokerUnavailable)


class _Broker:
    """Full for the first *full* attempts, then hands out a sandbox."""

    def __init__(self, full: int) -> None:
        self.full = full
        self.attempts = 0

    async def acquire(self) -> str:
        self.attempts += 1
        if self.attempts <= self.full:
            raise BrokerUnavailable("sandbox capacity reached: 4 of 4 live")
        return "sandbox"


def _wait(seconds: float | None, notes: list[bool]) -> CapacityWait:
    async def note(waiting: bool) -> None:
        notes.append(waiting)

    return CapacityWait(deadline=None if seconds is None else time.monotonic() + seconds, note=note)


async def test_a_full_broker_is_waited_out_with_the_queued_note() -> None:
    broker, notes = _Broker(full=2), []
    with capacity_wait(_wait(10, notes)):
        assert await retry_while_full(broker.acquire, _full) == "sandbox"
    assert broker.attempts == 3
    assert notes == [True, False]


async def test_past_the_wait_the_refusal_is_the_answer() -> None:
    broker, notes = _Broker(full=1_000), []
    with capacity_wait(_wait(0.6, notes)), pytest.raises(BrokerUnavailable):
        await retry_while_full(broker.acquire, _full)
    # Polled at 0.5 s, then cut short at the deadline: tried more than once, never until the broker gave in.
    assert 2 <= broker.attempts < 1_000
    assert notes == [True, False]


async def test_outside_a_slot_the_first_refusal_is_the_answer() -> None:
    broker = _Broker(full=1)
    with pytest.raises(BrokerUnavailable):
        await retry_while_full(broker.acquire, _full)
    assert broker.attempts == 1


async def test_other_refusals_are_not_waited_for() -> None:
    notes: list[bool] = []

    async def refused() -> str:
        raise SandboxError("bad spec")

    with capacity_wait(_wait(10, notes)), pytest.raises(SandboxError):
        await retry_while_full(refused, _full)
    assert notes == []


async def test_the_slot_sets_the_wait_for_the_call_inside_it() -> None:
    from app.agent_loop_lib.sandbox import capacity_wait as module
    from app.agent_loop_lib.sandbox.manager import SandboxManager, SandboxType

    notes: list[tuple[str, bool]] = []

    async def on_slot_wait(tool: str, *, waiting: bool) -> None:
        notes.append((tool, waiting))

    manager = SandboxManager(slot_wait_s=30, on_slot_wait=on_slot_wait)
    assert module._current.get() is None
    async with manager.slot(SandboxType.CODING, "unknown", tool="run_code"):
        wait = module._current.get()
        assert wait is not None and wait.deadline is not None
        assert 25 < wait.deadline - time.monotonic() <= 30
        await wait.note(True)
    assert module._current.get() is None
    assert notes == [("run_code", True)]
