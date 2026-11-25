"""WALLCLOCK-1's test helpers: ``thread_cpu`` reads this thread's work only, ``settle`` ends only a hang, ``on_virtual_time``
runs an async test on the loop's own clock."""

from __future__ import annotations

import asyncio
import gc
import threading

import pytest

from harness_core.testing.virtual_time import VirtualTimeLoop, on_virtual_time
from tests.support import settle as settle_module
from tests.support.cpu_budget import thread_cpu
from tests.support.settle import settle


def test_thread_cpu_pauses_the_collector_for_the_block_and_restores_it() -> None:
    assert gc.isenabled()
    with thread_cpu():
        assert not gc.isenabled()
    assert gc.isenabled()


def test_thread_cpu_leaves_a_disabled_collector_disabled() -> None:
    gc.disable()
    try:
        with thread_cpu():
            pass
        assert not gc.isenabled()
    finally:
        gc.enable()


def test_thread_cpu_does_not_count_time_spent_off_the_cpu() -> None:
    blocked = threading.Event()
    with thread_cpu() as cpu:
        blocked.wait(0.2)
    assert cpu.seconds < 0.1


def test_thread_cpu_counts_the_work_of_its_thread() -> None:
    with thread_cpu() as cpu:
        total = sum(i * i for i in range(300_000))
    assert total > 0
    assert cpu.seconds > 0


async def test_settle_returns_what_finishes() -> None:
    async def answer() -> int:
        await asyncio.sleep(0)
        return 42

    assert await settle(answer()) == 42


async def test_settle_ends_a_hang_with_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settle_module, "SETTLE_S", 0.01)
    with pytest.raises(TimeoutError):
        await settle(asyncio.Event().wait())


@on_virtual_time
async def test_on_virtual_time_runs_the_test_on_the_virtual_loop(tmp_path: object) -> None:
    loop = asyncio.get_running_loop()
    assert isinstance(loop, VirtualTimeLoop)
    started = loop.time()
    await asyncio.sleep(3600)
    assert loop.time() - started == 3600
    assert tmp_path is not None


@on_virtual_time
class TestOnVirtualTimeOnAClass:
    async def test_each_async_test_of_the_class_runs_on_it(self) -> None:
        assert isinstance(asyncio.get_running_loop(), VirtualTimeLoop)
