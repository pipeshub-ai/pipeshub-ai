"""P7.3 × P4: with the context compiler on (``HARNESS_CONTEXT_COMPILER``), recalled memory is the
first part of the volatile tail: never in the cached system, pinned or history bands, the same on
every turn, and the run's recall still journaled once."""

from __future__ import annotations

from harness_core.context.compiler import TAIL_HEADER
from harness_core.logs import HarnessLog
from harness_core.memories import (
    MEMORY_SLOT,
    InMemoryMemorySource,
    MemoryContextHost,
    MemoryRecallStep,
    RecalledMemory,
    render_memories,
)
from tests.unit.harness_core.context.kernel_support import (
    SYSTEM,
    Host,
    answered,
    compiler,
    looked_up,
)
from tests.unit.harness_core.durable.support import World, scripted

LOG = HarnessLog.named("tests.memories")
PREF = RecalledMemory(
    "mem_pref", "user", "preference", "Prefers answers in German.", {"complete": True, "sources": []}
)


async def test_cb1_the_memory_is_the_tail_s_first_part_on_every_turn_and_never_a_cached_band() -> None:
    world = World()
    world.admit(text="what should I wear?")
    text = render_memories([PREF]).text
    _, port = scripted(looked_up(500, "weather"), answered(600))
    kernel = world.kernel(
        port,
        pre_run=[MemoryRecallStep(InMemoryMemorySource([PREF]), log=LOG)],
        context=compiler(MemoryContextHost(Host())),
    )
    assert (await world.run(kernel)).status == "completed"
    assert len(port.calls) == 2
    for turn, call in enumerate(port.calls, start=1):
        last = call.messages[-1]
        assert isinstance(last.content, str)
        assert last.content.startswith(f"{TAIL_HEADER}\n\n{text}")
        assert last.content.endswith(f"Now: turn {turn}.")
        assert call.system == SYSTEM
        assert all(text not in str(m.content) for m in call.messages[:-1])
    saved = [e for e in world.events("loop.state_saved") if MEMORY_SLOT in (e.data.get("slots") or {})]  # type: ignore[operator]
    assert len(saved) == 1


async def test_cb2_without_a_recall_the_host_s_bands_are_unchanged() -> None:
    world = World()
    world.admit(text="hello")
    _, port = scripted(answered(100))
    kernel = world.kernel(port, context=compiler(MemoryContextHost(Host())))
    assert (await world.run(kernel)).status == "completed"
    assert port.calls[0].messages[-1].content == f"{TAIL_HEADER}\n\nNow: turn 1."
