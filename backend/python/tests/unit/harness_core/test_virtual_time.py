from __future__ import annotations

import asyncio
import contextlib
import selectors
import threading
import time
from typing import TYPE_CHECKING

from harness_core.testing.virtual_time import run_on_virtual_time

if TYPE_CHECKING:
    import pytest


def test_the_clock_moves_by_the_deadlines_waited_on_and_not_by_work(monkeypatch: pytest.MonkeyPatch) -> None:
    async def main() -> tuple[float, float]:
        loop = asyncio.get_running_loop()
        start = loop.time()
        time.sleep(0.05)  # the process busy or paused: no deadline passed
        busy = loop.time() - start
        await asyncio.sleep(3600)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.Event().wait(), 1.5)
        return busy, loop.time() - start

    real_select = selectors.DefaultSelector.select
    blocked: list[float | None] = []

    def select(self: selectors.BaseSelector, timeout: float | None = None) -> list[tuple[selectors.SelectorKey, int]]:
        if timeout != 0:
            blocked.append(timeout)
        return real_select(self, timeout)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(selectors.DefaultSelector, "select", select)
    busy, waited = run_on_virtual_time(main)
    assert busy == 0.0
    assert waited == 3601.5
    assert blocked == [], "the loop waited on the host's clock for a deadline"


def test_a_wakeup_from_another_thread_is_served_without_a_timer() -> None:
    async def main() -> str:
        loop = asyncio.get_running_loop()
        done: asyncio.Future[str] = loop.create_future()
        threading.Timer(0.05, lambda: loop.call_soon_threadsafe(done.set_result, "woken")).start()
        return await done

    assert run_on_virtual_time(main) == "woken"
