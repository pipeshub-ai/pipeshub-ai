"""The governor never blocks the event loop, even with the most slot files.

A slow-callback detector (asyncio debug mode) records every loop step that
took longer than a few milliseconds while hundreds of runs wait, are
granted, and release on 1024 slot files another worker also holds.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import random
import threading
from typing import TYPE_CHECKING

from app.agent_loop_lib.sandbox.capacity import FixedLimit, SandboxCapacity
from app.agent_loop_lib.sandbox.governor import (
    SandboxLimitExceeded,
    SandboxResourceGovernor,
)
from app.services.resource_governor.shared_slots import SharedSlots

if TYPE_CHECKING:
    from collections import Counter
    from pathlib import Path

# One pass over 1024 files takes about 1-2 ms. The bound is loose so a loaded
# CI machine (coverage on, thousands of tests in the same worker) passes, and
# it still catches a spin or a blocking lock, which wait up to a second. The
# garbage collector is off while the loop runs, so a collection pause is not
# taken for a blocked loop.
_SLOW_S = 0.5


class _SlowSteps(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage()
        # The test's own main task starts 300 tasks in one step (slow in debug
        # mode); only the runs' steps are the governor's.
        if "took" in text and "Executing" in text and "<locals>.mai" not in text:
            self.lines.append(text)


def test_hundreds_of_waiters_on_1024_files_never_stall_the_loop(tmp_path: Path, monkeypatch) -> None:
    files = 1024
    other = SharedSlots(tmp_path / "slots", files)
    mine = SharedSlots(tmp_path / "slots", files)
    capacity = SandboxCapacity.fixed(12, slot_files=files)
    governor = SandboxResourceGovernor(
        capacity, mine, limit_provider=FixedLimit(capacity), poll_s=0.02, rng=random.Random(3),
    )
    # The other worker holds half the cap, tagged, so every pass reads tags.
    held = [other.try_acquire_tagged(12, tag=b"other", tag_limit=None) for _ in range(6)]

    loop_thread: list[int] = []
    spins: list[float] = []
    original = SharedSlots.held_by_tag

    def watched(self: SharedSlots, budget: float = 0.0) -> Counter[bytes]:
        if budget > 0 and threading.get_ident() in loop_thread:
            spins.append(budget)
        return original(self, budget)

    monkeypatch.setattr(SharedSlots, "held_by_tag", watched)
    slow = _SlowSteps()
    asyncio_log = logging.getLogger("asyncio")
    asyncio_log.addHandler(slow)
    previous_level = asyncio_log.level
    asyncio_log.setLevel(logging.WARNING)

    async def one(rng: random.Random) -> None:
        try:
            lease = await governor.acquire(org_id=rng.choice(["a", "b"]), wait_s=rng.choice([0.05, 5.0]))
        except SandboxLimitExceeded:
            return
        await asyncio.sleep(rng.uniform(0, 0.01))
        await governor.release(lease)

    async def main() -> None:
        loop_thread.append(threading.get_ident())
        loop = asyncio.get_running_loop()
        loop.slow_callback_duration = _SLOW_S
        rng = random.Random(11)
        await asyncio.gather(*(one(rng) for _ in range(300)))
        governor.snapshot()

    gc.collect()
    gc.disable()
    try:
        asyncio.run(main(), debug=True)
    finally:
        gc.enable()
        asyncio_log.removeHandler(slow)
        asyncio_log.setLevel(previous_level)
        for hold in held:
            hold.release()
        other.close()
        mine.close()

    assert not spins, "held_by_tag spun on the event loop"
    assert not slow.lines, slow.lines[:3]
