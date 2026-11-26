"""Per-call slots through ``SandboxManager`` under load, Stop and cancels.

Each manager is one chat. For every call: the waiting note is shown at most
once, and cleared once if it was shown; it is never shown for a call that
got its slot at once; and every slot comes back.
"""

from __future__ import annotations

import asyncio
import random
from collections import defaultdict

from app.agent_loop_lib.core.context import CancellationToken
from app.agent_loop_lib.sandbox.governor import (
    GovernorLimits,
    SandboxLimitExceeded,
    SandboxResourceGovernor,
)
from app.agent_loop_lib.sandbox.manager import (
    SandboxCallStopped,
    SandboxManager,
    SandboxType,
)


class _PerCall:
    class capabilities:
        is_metered = False

    def __init__(self) -> None:
        self.sandbox_id = f"pc-{id(self)}"

    async def provision(self) -> None:
        await asyncio.sleep(0)

    async def destroy(self) -> None:
        await asyncio.sleep(0)


async def test_wait_notes_pair_up_and_slots_come_back_under_stop_and_cancel() -> None:
    rng = random.Random(21)
    governor = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=3))
    notes: dict[str, list[bool]] = defaultdict(list)
    running = 0
    peak = 0
    outcomes: dict[str, int] = defaultdict(int)

    async def chat(n: int) -> None:
        nonlocal running, peak
        token = CancellationToken()

        async def note(tool: str, waiting: bool) -> None:
            notes[tool].append(waiting)
            await asyncio.sleep(0)

        mgr = SandboxManager(
            governor=governor, slot_wait_s=rng.choice([0.0, 0.02, 3.0]), on_slot_wait=note,
            stop_token=token,
        )
        mgr.register_backend_factory(SandboxType.CODING, _PerCall)
        sid, _ = await mgr.get_or_create(SandboxType.CODING)
        tool = f"run_code-{n}"

        async def call() -> None:
            nonlocal running, peak
            async with mgr.slot(SandboxType.CODING, sid, tool=tool):
                running += 1
                peak = max(peak, running)
                try:
                    await asyncio.sleep(rng.uniform(0, 0.02))
                finally:
                    running -= 1

        if rng.random() < 0.3:
            asyncio.get_running_loop().call_later(rng.uniform(0, 0.05), token.cancel)
        try:
            await mgr.unless_stopped(call, tool=tool)
            outcomes["ran"] += 1
        except SandboxCallStopped:
            outcomes["stopped"] += 1
        except SandboxLimitExceeded:
            outcomes["refused"] += 1
        finally:
            await mgr.destroy_all()

    tasks = [asyncio.ensure_future(chat(n)) for n in range(150)]
    for _ in range(30):
        await asyncio.sleep(rng.uniform(0, 0.01))
        rng.choice(tasks).cancel()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert all(r is None or isinstance(r, asyncio.CancelledError) for r in results), results

    assert peak <= 3
    assert governor.held_total() == 0
    assert outcomes["ran"] > 0
    assert notes, "no call ever waited: the test lost its point"
    for tool, seen in notes.items():
        assert seen in ([True, False],), (tool, seen)
