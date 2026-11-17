"""A sandbox that keeps its slot for life gives it back even when the
teardown is cancelled.

A client disconnect cancels the request's cleanup, which is where
``destroy_all()`` runs. The slot of a sandbox being destroyed at that moment
must still be released, or this worker counts it until it exits.
"""

from __future__ import annotations

import asyncio

import pytest

from app.agent_loop_lib.sandbox.governor import GovernorLimits, SandboxResourceGovernor
from app.agent_loop_lib.sandbox.manager import (
    SandboxLimits,
    SandboxManager,
    SandboxType,
)


class _SlowDestroy:
    """A metered backend (no ``capabilities``: holds its slot for life)."""

    destroying: asyncio.Event

    def __init__(self) -> None:
        self.sandbox_id = f"slow-{id(self)}"
        self.destroyed = False

    async def provision(self) -> None:
        await asyncio.sleep(0)

    async def destroy(self) -> None:
        _SlowDestroy.destroying.set()
        await asyncio.sleep(0.05)
        self.destroyed = True


@pytest.fixture
def governor() -> SandboxResourceGovernor:
    _SlowDestroy.destroying = asyncio.Event()
    return SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=4))


def _manager(governor: SandboxResourceGovernor, limits: SandboxLimits | None = None) -> SandboxManager:
    mgr = SandboxManager(governor=governor)
    mgr.register_backend_factory(SandboxType.CODING, _SlowDestroy, limits=limits)
    return mgr


async def _cancel_once_destroying(coro) -> None:
    task = asyncio.ensure_future(coro)
    await _SlowDestroy.destroying.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_cancel_during_destroy_all_still_frees_every_slot(governor) -> None:
    mgr = _manager(governor)
    backends = [(await mgr.get_or_create(SandboxType.CODING))[1] for _ in range(3)]
    assert governor.snapshot()["total"] == 3

    await _cancel_once_destroying(mgr.destroy_all())
    await asyncio.sleep(0.3)

    assert governor.snapshot()["total"] == 0
    assert all(b.destroyed for b in backends)


async def test_a_cancel_during_destroy_still_frees_the_slot(governor) -> None:
    mgr = _manager(governor)
    sid, backend = await mgr.get_or_create(SandboxType.CODING)

    await _cancel_once_destroying(mgr.destroy(SandboxType.CODING, sid))
    await asyncio.sleep(0.1)

    assert governor.snapshot()["total"] == 0
    assert backend.destroyed


async def test_a_cancel_during_the_stale_sweep_still_frees_the_slot(governor) -> None:
    mgr = _manager(governor, SandboxLimits(max_lifetime_s=0.0))
    await mgr.get_or_create(SandboxType.CODING)
    await asyncio.sleep(0.01)

    # The next creation sweeps the first sandbox, and is cancelled mid-destroy.
    await _cancel_once_destroying(mgr.get_or_create(SandboxType.CODING))
    await asyncio.sleep(0.1)

    assert governor.snapshot()["total"] == 0


async def test_destroy_all_after_a_cancelled_one_does_not_double_release(governor) -> None:
    mgr = _manager(governor)
    for _ in range(2):
        await mgr.get_or_create(SandboxType.CODING)
    other = await governor.acquire()

    await _cancel_once_destroying(mgr.destroy_all())
    await mgr.destroy_all()
    await asyncio.sleep(0.2)

    assert governor.snapshot()["total"] == 1  # only the other caller's slot
    await governor.release(other)
    assert governor.snapshot()["total"] == 0
