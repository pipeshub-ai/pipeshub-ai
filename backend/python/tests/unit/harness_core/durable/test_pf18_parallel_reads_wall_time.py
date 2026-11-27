"""PF-18 (C7.08): a loop run's parallel reads take about as long as the slowest one, not the sum.

One model turn asks three reads that take 100, 200 and 300 ms (real time). They overlap, the batch is done in about
300 ms (well under the 600 ms a serialized batch takes), and each read is journaled once.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from harness_core.kernel.durable import ToolExecution

from .support import World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.kernel.durable import ToolContext

DELAYS_MS = (100, 200, 300)


async def test_pf18_three_parallel_reads_finish_in_about_the_slowest() -> None:
    world = World()
    spans: dict[str, tuple[float, float]] = {}

    async def slow(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        started = time.monotonic()
        await asyncio.sleep(int(str(args["ms"])) / 1000)
        spans[str(args["ms"])] = (started, time.monotonic())
        return ToolExecution(status="ok", content={"waited": args["ms"]})

    world.tools.add("slow_read", slow, tags=[("category", "read")])
    world.admit()
    model, port = scripted(reply("", *[("slow_read", {"ms": ms}) for ms in DELAYS_MS]), reply("done"))
    done = await world.run(world.kernel(port))
    assert (done.status, model.remaining) == ("completed", 0)
    starts = [s for s, _ in spans.values()]
    ends = [e for _, e in spans.values()]
    assert len(spans) == 3
    assert max(starts) < min(ends), "a read started only after another finished"
    batch = max(ends) - min(starts)
    assert 0.29 <= batch < 0.4, f"the batch took {batch:.3f} s: the reads did not run at once"
    assert len(world.events("tool.outcome")) == 3
