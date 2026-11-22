"""The embedding server's default executor must not cap the encode slots (``PIPESHUB_CAPACITY_PROFILE=auto``)."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from app import embedding_main

if TYPE_CHECKING:
    import pytest


async def _concurrent_threads(n: int) -> int:
    """How many of ``n`` calls to the loop's default executor run at once: they all wait on one barrier."""
    barrier = threading.Barrier(n, timeout=5)
    loop = asyncio.get_running_loop()
    try:
        await asyncio.gather(*(loop.run_in_executor(None, barrier.wait) for _ in range(n)))
    except threading.BrokenBarrierError:
        return barrier.n_waiting
    return n


async def test_slots_not_capped_by_default_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPESHUB_CAPACITY_PROFILE", "auto")
    slots, reserved = 74, 1
    loop = asyncio.get_running_loop()
    embedding_main.configure_default_executor(loop, slots, reserved)
    assert await _concurrent_threads(slots) == slots  # every slot's encode runs at once


async def test_the_stdlib_default_executor_would_cap_them(monkeypatch: pytest.MonkeyPatch) -> None:
    """The control: without the sizing (stdlib default ``min(32, cpus + 4)``), 74 simultaneous encodes cannot run."""
    monkeypatch.delenv("PIPESHUB_CAPACITY_PROFILE", raising=False)
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=32))
    embedding_main.configure_default_executor(loop, 74, 1)  # flag off: leaves the 32 as they are
    assert await _concurrent_threads(74) < 74


def test_executor_threads_are_slots_plus_reserved_plus_two() -> None:
    assert embedding_main.default_executor_threads(74, 1) == 77
    assert embedding_main.default_executor_threads(8, 0) == 10
